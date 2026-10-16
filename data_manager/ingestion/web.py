"""
Flask blueprint for DfT source ingestion: Upload -> Identify -> Validate -> Preview -> Save to staging.

Register with init_app(app). The upload endpoint streams the raw request body to disk in 1 MB
chunks (no multipart parsing, no buffering in memory), hashing as it goes, and hands the file
to the background JobRunner. All POST endpoints require a JSON body, which browsers cannot send
cross-site without a CORS pre-flight that this application never grants.
"""

import hashlib
import os
import re
import threading
import time

from flask import Blueprint, current_app, jsonify, render_template, request

from . import contracts
from .config import Settings
from .jobs import JobRunner, QueueFull
from .registry import Registry, StagingRefused, sha256_file

bp = Blueprint("ingest", __name__)
ALLOWED_EXT = ("csv", "xlsx", "ods")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9 ._()\-]")


class IngestState:
    def __init__(self, settings, overrides=None):
        self.settings = settings
        self.registry = Registry(settings)
        self.runner = JobRunner(settings, overrides)
        self.uploads = threading.BoundedSemaphore(settings.max_concurrent_uploads)
        self.active_uploads = 0
        self.dedupe = threading.Lock()
        self._prod_cache = {}


def init_app(app, settings=None, overrides=None):
    """overrides: Settings fields also passed to worker processes (tests use disposable roots)."""
    if settings is None:
        settings = Settings()
    app.extensions["lct_ingest"] = IngestState(settings, overrides)
    app.register_blueprint(bp)
    return app.extensions["lct_ingest"]


def _st():
    st = current_app.extensions["lct_ingest"]
    st.runner.start()            # lazy: never starts in the reloader's parent process
    return st


def safe_filename(name):
    base = os.path.basename((name or "").replace("\\", "/")).strip()
    base = _SAFE_NAME.sub("_", base)[:200]
    return base if base and base not in (".", "..") else ""


def _json_body():
    if not request.is_json:
        return None
    return request.get_json(silent=True) or {}


def _err(status, message, **extra):
    return jsonify({"ok": False, "error": message, **extra}), status


# ------------------------------------------------------------------ production sources (read-only)
def production_sources(st):
    """Current approved source per dataset: the file in ev/, its hash and header-derived latest quarter."""
    out = []
    for c in contracts.CONTRACTS.values():
        p = os.path.join(st.settings.production_ev_dir, c.production_file)
        item = {"dataset": c.dataset, "title": c.title, "role": c.role, "file": f"ev/{c.production_file}", "present": os.path.exists(p)}
        if item["present"]:
            key = (p, os.path.getsize(p), os.path.getmtime(p))
            cached = st._prod_cache.get(c.dataset)
            if not cached or cached[0] != key:
                with open(p, encoding="utf-8-sig", errors="replace") as f:
                    header = f.readline().strip().split(",")
                quarters = [h for h in header if contracts.QUARTER_RE.match(h)]
                cached = (key, {"sha256": sha256_file(p), "size_bytes": key[1],
                                "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(key[2])),
                                "latest_quarter": quarters[0] if quarters else None})
                st._prod_cache[c.dataset] = cached
            item.update(cached[1])
        out.append(item)
    return out


# ------------------------------------------------------------------ pages
@bp.route("/dft-ingest")
def ingest_page():
    st = _st()
    return render_template("dft_ingest.html", production=production_sources(st), jobs=st.registry.jobs(30),
                           max_upload_mb=st.settings.max_upload_bytes // (1024 * 1024),
                           datasets=list(contracts.CONTRACTS))


@bp.route("/dft-sources")
def sources_page():
    st = _st()
    versions = st.registry.versions()
    by_id = {v["id"]: v for v in versions}
    for v in versions:
        v["supersedes_label"] = by_id.get(v["supersedes_version_id"], {}).get("label")
        v["restates_label"] = by_id.get(v["restates_version_id"], {}).get("label")
    staged = {}
    for v in versions:
        if v["status"] == "STAGED":
            staged.setdefault(v["dataset"], v)        # newest staged per dataset (ordered by id desc)
    common = None
    if staged:
        idx = [contracts.quarter_index(v["period_label"]) for v in staged.values()]
        common = min(staged.values(), key=lambda v: contracts.quarter_index(v["period_label"]))["period_label"]
        common = {"period": common, "consistent": len(set(idx)) == 1}
    return render_template("dft_sources.html", production=production_sources(st), versions=versions,
                           latest_staged=staged, common=common, events=st.registry.events(limit=40))


@bp.route("/dft-sources/<int:version_id>")
def version_page(version_id):
    st = _st()
    v = st.registry.version(version_id)
    if not v:
        return render_template("dft_sources.html", production=production_sources(st), versions=[], latest_staged={},
                               common=None, events=[], error=f"version {version_id} not found"), 404
    manifest, profile = _version_files(st, v)
    return render_template("dft_version.html", v=v, manifest=manifest, profile=profile,
                           verified=st.registry.verify_version(version_id), events=st.registry.events(version_id=version_id))


def _version_files(st, v):
    import json
    d = st.registry.path(v["path"])
    out = []
    for name in ("manifest.json", "profile.json"):
        try:
            with open(os.path.join(d, name), encoding="utf-8") as f:
                out.append(json.load(f))
        except (OSError, ValueError):
            out.append(None)
    return out


# ------------------------------------------------------------------ upload (streamed)
def _refusal(st, name, length):
    """Reason to refuse an upload before any bytes are read, or None."""
    s = st.settings
    ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    if not name or ext not in ALLOWED_EXT:
        return 400, f"unsupported file type .{ext or '?'}; accepted: .csv, .xlsx, .ods"
    if length is None:
        return 411, "Content-Length is required"
    if length <= 0:
        return 400, "empty file"
    if length > s.max_upload_bytes:
        return 413, (f"file is {length / 2**20:,.0f} MB; the limit is {s.max_upload_bytes // 2**20:,} MB "
                     f"(set LCT_INGEST_MAX_UPLOAD_MB to change it)")
    if not st.runner.has_capacity():
        return 429, "the processing queue is full; try again when the current files have finished"
    return None


@bp.route("/api/ingest/precheck", methods=["POST"])
def api_precheck():
    """Called by the page before streaming: a refusal sent mid-body can be lost when the server closes the socket."""
    st = _st()
    body = _json_body()
    if body is None:
        return _err(415, "JSON body required")
    try:
        size = int(body.get("size"))
    except (TypeError, ValueError):
        size = None
    refused = _refusal(st, safe_filename(body.get("filename", "")), size)
    if refused:
        return _err(*refused, limit_bytes=st.settings.max_upload_bytes)
    if st.active_uploads >= st.settings.max_concurrent_uploads:   # advisory only; the PUT enforces the limit
        return _err(429, f"{st.settings.max_concurrent_uploads} uploads are already in progress")
    return jsonify({"ok": True, "limit_bytes": st.settings.max_upload_bytes})


@bp.route("/api/ingest/upload", methods=["PUT"])
def api_upload():
    st = _st()
    s = st.settings
    name = safe_filename(request.args.get("filename", ""))
    length = request.content_length
    refused = _refusal(st, name, length)
    if refused:
        return _err(*refused, limit_bytes=s.max_upload_bytes)
    if not st.uploads.acquire(blocking=False):
        return _err(429, f"{s.max_concurrent_uploads} uploads are already in progress")
    st.active_uploads += 1
    job_id = None
    try:
        job_id = st.registry.create_job(name, length)
        part = st.registry.upload_path(job_id, part=True)
        h, received, last = hashlib.sha256(), 0, 0.0
        stream = request.stream
        try:
            with open(part, "wb") as f:
                while True:
                    chunk = stream.read(s.upload_chunk_bytes)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > length:
                        raise ValueError("more bytes than Content-Length")
                    h.update(chunk)
                    f.write(chunk)
                    if time.time() - last > 0.5:
                        last = time.time()
                        st.registry.update_job(job_id, received_bytes=received, progress=0.0)
        except Exception as e:  # noqa: BLE001 - client disconnects surface as various exceptions
            _discard(st, job_id, part, f"upload interrupted after {received:,} of {length:,} bytes ({type(e).__name__})")
            return _err(400, "upload interrupted", job_id=job_id)
        if received != length:
            _discard(st, job_id, part, f"upload interrupted after {received:,} of {length:,} bytes")
            return _err(400, "upload interrupted", job_id=job_id)
        sha = h.hexdigest()
        with st.dedupe:
            version, other = st.registry.find_by_sha(sha)
            if other and other["id"] == job_id:
                other = None
            if version or other:
                what = f"version {version['label']}" if version else f"upload {other['id']} ({other['state']})"
                _discard(st, job_id, part, f"duplicate: identical to {what}", stage="duplicate")
                return _err(409, f"identical file already received as {what}", job_id=job_id, duplicate_of=what)
            os.replace(part, st.registry.upload_path(job_id))
            st.registry.update_job(job_id, received_bytes=received, sha256=sha)
        try:
            st.runner.submit(job_id)
        except QueueFull as e:
            return _err(429, str(e), job_id=job_id)
        return jsonify({"ok": True, "job_id": job_id, "sha256": sha, "bytes": received}), 202
    finally:
        st.active_uploads -= 1
        st.uploads.release()


def _discard(st, job_id, part, message, stage="upload"):
    if os.path.exists(part):
        os.remove(part)
    st.registry.finish_job(job_id, "FAILED", stage=stage, error=message)


# ------------------------------------------------------------------ status and review
def _public_job(st, job, full=True):
    job = dict(job)
    res = st.registry.result(job["id"]) if full else None
    if res:
        res.pop("traceback", None)
        job["result"] = res
    job["upload_present"] = os.path.exists(st.registry.upload_path(job["id"]))
    return job


@bp.route("/api/ingest/jobs")
def api_jobs():
    st = _st()
    return jsonify({"jobs": [_public_job(st, j, full=False) for j in st.registry.jobs(50)],
                    "runner": {"current": st.runner.current, "queued": st.runner.q.qsize(), "last_run": st.runner.last_run}})


@bp.route("/api/ingest/jobs/<job_id>")
def api_job(job_id):
    st = _st()
    job = st.registry.job(job_id)
    if not job:
        return _err(404, "unknown upload")
    return jsonify({"ok": True, "job": _public_job(st, job)})


def _requeue(st, job_id, **fields):
    job = st.registry.job(job_id)
    if not job:
        return _err(404, "unknown upload")
    if job["state"] not in ("READY", "FAILED"):
        return _err(409, f"upload is {job['state']}")
    if not os.path.exists(st.registry.upload_path(job_id)):
        return _err(409, "the uploaded file is no longer available; upload it again")
    st.registry.update_job(job_id, **fields)
    try:
        st.runner.submit(job_id)
    except QueueFull as e:
        return _err(429, str(e))
    return jsonify({"ok": True, "job_id": job_id}), 202


@bp.route("/api/ingest/jobs/<job_id>/retry", methods=["POST"])
def api_retry(job_id):
    st = _st()
    if _json_body() is None:
        return _err(415, "JSON body required")
    return _requeue(st, job_id)


@bp.route("/api/ingest/jobs/<job_id>/revalidate", methods=["POST"])
def api_revalidate(job_id):
    """Reviewer rejects the automatic identification and names the dataset (and optionally the worksheet)."""
    st = _st()
    body = _json_body()
    if body is None:
        return _err(415, "JSON body required")
    dataset = body.get("dataset")
    if dataset not in contracts.CONTRACTS:
        return _err(400, f"dataset must be one of {list(contracts.CONTRACTS)}")
    sheet = (body.get("sheet") or "").strip() or None
    return _requeue(st, job_id, dataset_override=dataset, sheet_override=sheet)


@bp.route("/api/ingest/jobs/<job_id>/reject", methods=["POST"])
def api_reject(job_id):
    st = _st()
    body = _json_body()
    if body is None:
        return _err(415, "JSON body required")
    try:
        st.registry.reject(job_id, (body.get("reason") or "").strip()[:500])
    except StagingRefused as e:
        return _err(409, str(e))
    return jsonify({"ok": True})


@bp.route("/api/ingest/jobs/<job_id>/stage", methods=["POST"])
def api_stage(job_id):
    """Save to staging: an immutable candidate source version. Does not approve or publish anything."""
    st = _st()
    body = _json_body()
    if body is None:
        return _err(415, "JSON body required")
    try:
        with st.dedupe:
            v = st.registry.stage(job_id, body.get("dataset"), body.get("period"),
                                  (body.get("restatement_reason") or "").strip()[:1000] or None)
    except StagingRefused as e:
        return _err(409, str(e))
    return jsonify({"ok": True, "version": v})


@bp.route("/api/ingest/versions")
def api_versions():
    st = _st()
    return jsonify({"versions": st.registry.versions(request.args.get("dataset"))})


@bp.route("/api/ingest/versions/<int:version_id>")
def api_version(version_id):
    st = _st()
    v = st.registry.version(version_id)
    if not v:
        return _err(404, "unknown version")
    manifest, profile = _version_files(st, v)
    return jsonify({"ok": True, "version": v, "manifest": manifest, "verified": st.registry.verify_version(version_id)})
