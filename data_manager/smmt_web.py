"""
Flask blueprint: SMMT entries -> Generate nowcast candidate (background job) -> Review -> Dashboard preview.

Every number shown comes from the selected candidate's sealed manifest and files. Nothing here publishes: the live
dashboard files and dashboard.html are pinned by the C1 release protections and are only ever read.
"""

import json
import os
import re
import threading
import uuid
from datetime import datetime, timezone

import pandas as pd
from flask import Blueprint, Response, abort, current_app, jsonify, redirect, render_template, request, send_file

from .smmt_candidate_builder import (CANDIDATES_REL, FORECAST_SOURCE, INTEGRATION_REL, CandidateError,
                                     SMmtCandidateBuilder)
from .smmt_dashboard_patch import patched_dashboard_from_repo

bp = Blueprint("smmt_nowcast", __name__)
CID_RE = re.compile(r"^SMMT-[A-Za-z0-9-]+$")
# The preview serves the candidate exactly as the publication step would publish it (same files and manifest)
PREVIEW_OVERLAY = {f"{INTEGRATION_REL}/smmt_nowcast_{g}.csv": f"smmt_nowcast_{g}.csv" for g in ("dno", "la", "laep")}
PREVIEW_MANIFEST = f"{INTEGRATION_REL}/smmt_nowcast_manifest.json"
DOWNLOADS = {"lsoa": "nowcast_lsoa.csv", "dno": "smmt_nowcast_dno.csv", "la": "smmt_nowcast_la.csv",
             "laep": "smmt_nowcast_laep.csv", "summary": "ukpn_monthly_summary.csv", "qa": "qa_checks.csv",
             "manifest": "manifest.json"}


class SmmtState:
    def __init__(self, builder):
        self.builder = builder
        self.lock = threading.Lock()
        self.jobs = {}
        self.running = None


def init_app(app, project_root, db_path=None):
    app.extensions["lct_smmt"] = SmmtState(SMmtCandidateBuilder(project_root, db_path))
    app.register_blueprint(bp)
    return app.extensions["lct_smmt"]


def _st() -> SmmtState:
    return current_app.extensions["lct_smmt"]


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cid(cid):
    if not CID_RE.match(cid or ""):
        abort(404)
    p = os.path.join(_st().builder.candidates_dir, cid, "manifest.json")
    if not os.path.exists(p):
        abort(404)
    return cid


def start_job(st: SmmtState) -> str:
    with st.lock:
        if st.running:
            raise CandidateError(f"a nowcast generation is already running ({st.running})")
        jid = uuid.uuid4().hex[:12]
        st.jobs[jid] = {"job_id": jid, "state": "RUNNING", "stage": "Queued", "stages": [], "started": _now(),
                        "candidate_id": None, "errors": []}
        st.running = jid

    def progress(stage):
        st.jobs[jid]["stage"] = stage
        st.jobs[jid]["stages"].append({"stage": stage, "at": _now()})

    def run():
        try:
            man = st.builder.generate(progress=progress, job_id=jid)
            st.jobs[jid].update(state="DONE" if man["status"] == "VALIDATED" else "FAILED",
                                candidate_id=man["candidate_id"], errors=man.get("errors", []), finished=_now())
        except Exception as e:  # noqa: BLE001
            st.jobs[jid].update(state="FAILED", errors=[f"{type(e).__name__}: {e}"], finished=_now())
        finally:
            with st.lock:
                st.running = None

    threading.Thread(target=run, daemon=True).start()
    return jid


def candidate_rows():
    b = _st().builder
    rows = []
    for m in b.list_candidates():
        st = b.status(m["candidate_id"])
        rows.append({"m": m, "state": st["state"], "detail": st["detail"]})
    return rows


@bp.route("/smmt-nowcast")
def page_list():
    b = _st().builder
    preview_error, plan = None, None
    try:
        inp = b.inputs()
        months, info = b.nowcast_months(inp["baseline_period"], inp["smmt_entries"])
        plan = {"baseline": inp["baseline_period"], "months": months, "info": info, "fingerprint": inp["fingerprint"],
                "entries": [e for e in inp["smmt_entries"] if f"{e['year']}-{e['month']:02d}" in months],
                "notes": inp["smmt_notes"]}
    except Exception as e:  # noqa: BLE001
        preview_error = f"{type(e).__name__}: {e}"
    return render_template("smmt_nowcast.html", plan=plan, preview_error=preview_error, candidates=candidate_rows(),
                           running=_st().running)


@bp.route("/api/smmt-nowcast/generate", methods=["POST"])
def api_generate():
    try:
        jid = start_job(_st())
    except CandidateError as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    return jsonify({"ok": True, "job_id": jid}), 202


@bp.route("/api/smmt-nowcast/job/<jid>")
def api_job(jid):
    job = _st().jobs.get(jid)
    if not job:
        return jsonify({"ok": False, "error": "unknown job"}), 404
    return jsonify({"ok": True, **job})


@bp.route("/smmt-nowcast/<cid>")
def page_candidate(cid):
    cid = _cid(cid)
    b = _st().builder
    man = b.load(cid)
    st = b.status(cid)
    tables = {}
    if man["status"] == "VALIDATED":
        folder = b.folder(cid)
        tables["summary"] = pd.read_csv(os.path.join(folder, "ukpn_monthly_summary.csv")).to_dict("records")
        tables["qa"] = pd.read_csv(os.path.join(folder, "qa_checks.csv"), keep_default_na=False).to_dict("records")
        dno = pd.read_csv(os.path.join(folder, "smmt_nowcast_dno.csv"))
        tables["dno"] = dno.pivot_table(index=["period"], columns=["DNO", "EV_Type"], values="value").sort_index()
    return render_template("smmt_candidate.html", m=man, st=st, t=tables, downloads=DOWNLOADS,
                           rejection=_rejection(cid))


def _rejection(cid):
    p = os.path.join(_st().builder.folder(cid), "rejection.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


@bp.route("/api/smmt-nowcast/<cid>/reject", methods=["POST"])
def api_reject(cid):
    cid = _cid(cid)
    body = request.get_json(silent=True) or {}
    try:
        rec = _st().builder.reject(cid, body.get("reason", ""))
    except CandidateError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify({"ok": True, **rec})


@bp.route("/smmt-nowcast/<cid>/download/<name>")
def download(cid, name):
    cid = _cid(cid)
    if name not in DOWNLOADS:
        abort(404)
    p = os.path.join(_st().builder.folder(cid), DOWNLOADS[name])
    if not os.path.exists(p):
        abort(404)
    return send_file(p, as_attachment=True, download_name=f"{cid}_{DOWNLOADS[name]}")


# ---------------------------------------------------------------- dashboard preview (read-only)
@bp.route("/smmt-preview/<cid>/")
@bp.route("/smmt-preview/<cid>/dashboard.html")
def preview_dashboard(cid):
    cid = _cid(cid)
    b = _st().builder
    man = b.load(cid)
    if man["status"] != "VALIDATED":
        abort(409, "only validated candidates can be previewed")
    try:
        html = patched_dashboard_from_repo(b.root)
    except ValueError as e:
        abort(409, str(e))
    banner = ('<div id="smmtPreviewBanner" style="background:#FFF3CD;color:#664D03;padding:10px 16px;font-family:sans-serif;'
              'border-bottom:2px solid #FFC107"><strong>PREVIEW &mdash; not published.</strong> SMMT provisional nowcast '
              f'candidate {cid} ({man["baseline_period"]} to {man["latest_nowcast_month"]}). The live dashboard is unchanged. '
              f'<a href="/smmt-nowcast/{cid}">Back to the candidate review</a></div>')
    html = re.sub(r"(<body[^>]*>)", lambda mt: mt.group(1) + banner, html, count=1)
    return Response(html, mimetype="text/html")


@bp.route("/smmt-preview/<cid>/<path:rel>")
def preview_file(cid, rel):
    """Files fetched by the preview page: candidate overlay for the EV forecast files, otherwise the live repository
    (read-only) under the dashboard's own relative paths."""
    cid = _cid(cid)
    b = _st().builder
    rel = rel.replace("\\", "/")
    if ".." in rel.split("/") or rel.startswith("/"):
        abort(404)
    if rel in PREVIEW_OVERLAY:
        return send_file(os.path.join(b.folder(cid), *PREVIEW_OVERLAY[rel].split("/")), mimetype="text/csv")
    if rel == PREVIEW_MANIFEST:
        return jsonify(b.publication_manifest(cid))
    if not rel.startswith("project/output_processed/"):
        abort(404)
    p = os.path.join(b.root, *rel.split("/"))
    if not os.path.isfile(p):
        abort(404)
    return send_file(p)


def latest_validated():
    for r in candidate_rows():
        if r["state"] == "VALIDATED":
            return r
    return None


__all__ = ["init_app", "start_job", "latest_validated", "candidate_rows", "FORECAST_SOURCE", "CANDIDATES_REL"]
