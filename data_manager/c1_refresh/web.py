"""
Flask blueprint: Validated VEH0135 -> Generate C1 Candidate -> QA -> Historical Revision Review -> Release Decision.

Generation runs in one bounded background process (memory cap, timeout, one at a time). Every number
shown comes from a sealed candidate package; nothing is published from these pages.
"""

import json
import os
import subprocess
import sys
import threading
import time
import uuid

from flask import Blueprint, abort, current_app, jsonify, render_template, request, send_file

from . import config as C
from . import sources, store
from .engine import verify_package
from data_manager.ingestion.config import Settings
from data_manager.ingestion.procmem import MemoryCap
from data_manager.ingestion.registry import Registry

bp = Blueprint("c1_refresh", __name__)
DOWNLOADS = {"qa": "reports/qa_checks.csv", "restatement": "reports/restatement_geography.csv",
             "restatement_lsoa": "reports/restatement_lsoa_changes.csv", "source_revisions": "reports/source_revisions.csv",
             "pools": "reports/national_pools_and_lpn_floor.csv", "lsoa_latest": "c1_lsoa_latest_vintage.csv",
             "summary": "reports/summary.json", "manifest": "manifest.json"}


class C1State:
    def __init__(self, ws, memory_mb=1500, timeout_s=3600):
        self.ws, self.memory_mb, self.timeout_s = ws, memory_mb, timeout_s
        self.lock = threading.Lock()
        self.running = None


def init_app(app, workspace=None, memory_mb=1500, timeout_s=3600):
    app.extensions["lct_c1"] = C1State(workspace or C.Workspace(), memory_mb, timeout_s)
    app.register_blueprint(bp)
    return app.extensions["lct_c1"]


def _st():
    return current_app.extensions["lct_c1"]


def registered_versions(ws):
    if not os.path.exists(os.path.join(ws.registry_root, "registry.db")):
        return []
    reg = Registry(Settings(registry_root=ws.registry_root))
    return [v for v in reg.versions("VEH0135") if v["status"] == "STAGED"]


def _job(ws, jid):
    p = ws.path("jobs", f"{jid}.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


@bp.route("/c1-refresh")
def page_list():
    st = _st()
    ws = st.ws.ensure()
    base = None
    for s in sources.list_sets(ws):
        p = ws.path("baseline", s["source_set_id"], "reproduction.json")
        if s["kind"] == "RELEASE_SOURCE" and os.path.exists(p):
            base = json.load(open(p, encoding="utf-8"))
    jobs = sorted((json.load(open(ws.path("jobs", n), encoding="utf-8")) for n in os.listdir(ws.path("jobs")) if n.endswith(".json")),
                  key=lambda j: j.get("started", ""), reverse=True)[:10]
    return render_template("c1_refresh.html", versions=registered_versions(ws), candidates=store.list_candidates(ws),
                           baseline=base, live_release=C.LIVE_RELEASE_ID, jobs=jobs, running=st.running)


@bp.route("/c1-refresh/<cid>")
def page_candidate(cid):
    st = _st()
    ws = st.ws
    root = ws.path("candidates", cid)
    if not cid.startswith("C1C-") or not os.path.isdir(root):
        abort(404)
    man = json.load(open(os.path.join(root, "manifest.json"), encoding="utf-8"))
    summary = json.load(open(os.path.join(root, "reports", "summary.json"), encoding="utf-8"))
    ok, detail = verify_package(ws, cid)
    sset = sources.load(ws, man["source_set_id"])
    last = man["historical_periods"][-1]
    uk = {r["EV_Type"]: r["published"] for r in summary["restatement_ukpn"] if r["period"] == last}
    live_last = {"UKPN": uk["Total"], "BEV": uk["BEV"], "PHEV": uk["PHEV"],
                 **{r["geography"]: r["published"] for r in summary["restatement_dno"] if r["period"] == last}}
    from . import release_readiness
    ready = release_readiness.readiness(ws, cid)
    return render_template("c1_candidate.html", m=man, s=summary, live_last=live_last, ready=ready, running=st.running,
                           state=store.state(ws, cid), decisions=store.decisions(ws, cid),
                           chain_ok=store.verify_chain(ws, cid), sealed_ok=ok, seal_detail=detail, sset=sset,
                           options=store.OPTIONS, downloads=DOWNLOADS)


@bp.route("/c1-refresh/<cid>/download/<name>")
def download(cid, name):
    st = _st()
    if name not in DOWNLOADS or not cid.startswith("C1C-"):
        abort(404)
    p = st.ws.path("candidates", cid, *DOWNLOADS[name].split("/"))
    if not os.path.exists(p):
        abort(404)
    return send_file(p, as_attachment=True, download_name=f"{cid}_{os.path.basename(p)}")


@bp.route("/api/c1-refresh/generate", methods=["POST"])
def api_generate():
    st = _st()
    body = request.get_json(silent=True) if request.is_json else None
    if body is None:
        return jsonify({"ok": False, "error": "JSON body required"}), 415
    label = body.get("version_label")
    if label not in {v["label"] for v in registered_versions(st.ws)}:
        return jsonify({"ok": False, "error": "unknown or unvalidated source version"}), 400
    with st.lock:
        if st.running:
            return jsonify({"ok": False, "error": f"generation {st.running} is already running"}), 429
        jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        st.running = jid
    with open(st.ws.path("jobs", f"{jid}.json"), "w", encoding="utf-8") as f:
        json.dump({"job": jid, "state": "QUEUED", "version_label": label, "started": sources.now()}, f)
    threading.Thread(target=_run, args=(st, jid, label), daemon=True).start()
    return jsonify({"ok": True, "job": jid}), 202


@bp.route("/api/c1-refresh/<cid>/dry-run", methods=["POST"])
def api_dry_run(cid):
    """Disposable dry run of the approved release (never production; the release tool refuses DRY_RUN on the live root)."""
    st = _st()
    if (request.get_json(silent=True) if request.is_json else None) is None:
        return jsonify({"ok": False, "error": "JSON body required"}), 415
    if not cid.startswith("C1C-") or not os.path.isdir(st.ws.path("candidates", cid)):
        return jsonify({"ok": False, "error": "unknown candidate"}), 404
    if store.state(st.ws, cid)["state"] != "APPROVED_FOR_FUTURE_RELEASE":
        return jsonify({"ok": False, "error": "record an option B decision first"}), 409
    with st.lock:
        if st.running:
            return jsonify({"ok": False, "error": f"job {st.running} is already running"}), 429
        jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        st.running = jid
    with open(st.ws.path("jobs", f"{jid}.json"), "w", encoding="utf-8") as f:
        json.dump({"job": jid, "state": "QUEUED", "kind": "dry-run", "candidate_id": cid, "started": sources.now()}, f)
    threading.Thread(target=_run, args=(st, jid, cid, "dry-run"), daemon=True).start()
    return jsonify({"ok": True, "job": jid}), 202


def _run(st, jid, label, command="generate"):
    env = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "PYTHONDONTWRITEBYTECODE": "1", "LCT_C1_JOB": jid, "LCT_C1_WORKSPACE": st.ws.root}
    cap = MemoryCap(st.memory_mb)
    try:
        p = subprocess.Popen([sys.executable, "-m", "data_manager.c1_refresh.cli", command, label], cwd=C.PROJECT_ROOT, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        cap.assign(p)
        try:
            out, _ = p.communicate(timeout=st.timeout_s)
            err = None if p.returncode == 0 else out.decode("utf-8", "replace")[-800:]
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()
            err = f"exceeded the {st.timeout_s} s time limit"
        peak = cap.peak_mb()
        j = _job(st.ws, jid) or {}
        if j.get("state") not in ("COMPLETE",) and not (command == "dry-run" and j.get("state") == "FAILED" and j.get("dry_run_pass") is False):
            if peak and peak >= st.memory_mb - 32:
                err = f"exceeded the {st.memory_mb} MB memory limit"
            j.update(state="FAILED", error=j.get("error") or err or "worker stopped", finished=sources.now())
        j["worker_peak_commit_mb"] = peak
        with open(st.ws.path("jobs", f"{jid}.json"), "w", encoding="utf-8") as f:
            json.dump(j, f, indent=1, default=str)
    finally:
        cap.close()
        st.running = None


@bp.route("/api/c1-refresh/jobs/<jid>")
def api_job(jid):
    j = _job(_st().ws, jid)
    return (jsonify({"ok": True, "job": j}) if j else (jsonify({"ok": False}), 404))


@bp.route("/api/c1-refresh/<cid>/decision", methods=["POST"])
def api_decision(cid):
    st = _st()
    body = request.get_json(silent=True) if request.is_json else None
    if body is None:
        return jsonify({"ok": False, "error": "JSON body required"}), 415
    try:
        rec = store.decide(st.ws, cid, body.get("option"), body.get("decided_by"), body.get("evidence"), body.get("confirmation"),
                           body.get("restatement_approval"))
    except store.DecisionRefused as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    return jsonify({"ok": True, "decision": rec})
