"""
Source registry: upload jobs, immutable source versions and an audit log.

Layout (registry_root, default <repo>/source_registry, git-ignored by its own .gitignore):

  registry.db                       SQLite (WAL): jobs, versions, events
  incoming/<job>.part               upload in progress (deleted if interrupted)
  incoming/<job>.upload             complete upload awaiting review
  work/<job>/                       canonical.csv, result.json written by the worker
  versions/<DATASET>/<YYYY_Qn>/<label>/
      original.<ext> canonical.csv profile.json manifest.json   (read-only once written)
  failed/<job>/                     error.json and, when small enough, the original bytes
  production_profiles/<sha256>.json cached profiles of the current ev/ sources

Versions are never overwritten or deleted. A staged version records the version it supersedes
(previous version of the same dataset) and, for a same-quarter DfT revision, the version it
restates. Staging is not approval: the approved source remains the production file in ev/ until
a separate, authorised release replaces it.
"""

import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import uuid
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    stage TEXT,
    progress REAL DEFAULT 0,
    message TEXT,
    original_filename TEXT NOT NULL,
    declared_bytes INTEGER,
    received_bytes INTEGER DEFAULT 0,
    sha256 TEXT,
    format TEXT,
    dataset TEXT,
    sheet TEXT,
    period_label TEXT,
    dataset_override TEXT,
    sheet_override TEXT,
    error TEXT,
    result_path TEXT,
    version_id INTEGER,
    attempts INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT UNIQUE NOT NULL,
    dataset TEXT NOT NULL,
    period_label TEXT NOT NULL,
    period_end TEXT NOT NULL,
    sha256 TEXT NOT NULL UNIQUE,
    canonical_sha256 TEXT,
    original_filename TEXT NOT NULL,
    format TEXT NOT NULL,
    sheet TEXT,
    size_bytes INTEGER NOT NULL,
    rows INTEGER,
    status TEXT NOT NULL,
    supersedes_version_id INTEGER REFERENCES versions(id),
    restates_version_id INTEGER REFERENCES versions(id),
    restatement_reason TEXT,
    production_sha256 TEXT,
    production_latest TEXT,
    job_id TEXT,
    path TEXT NOT NULL,
    manifest_sha256 TEXT,
    uploaded_at TEXT,
    staged_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    job_id TEXT,
    version_id INTEGER,
    event TEXT NOT NULL,
    detail TEXT
);
"""

ACTIVE_STATES = ("UPLOADING", "QUEUED", "PARSING", "VALIDATING")
TERMINAL_STATES = ("READY", "FAILED", "REJECTED", "STAGED")


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class StagingRefused(ValueError):
    """Duplicate, conflicting or invalid staging request."""


class Registry:
    def __init__(self, settings):
        self.settings = settings
        self.root = os.path.abspath(settings.registry_root)
        self._ready = False

    # -------------------------------------------------------------- setup
    def ensure(self):
        if self._ready:
            return
        for d in ("incoming", "work", "versions", "failed", "production_profiles"):
            os.makedirs(os.path.join(self.root, d), exist_ok=True)
        gi = os.path.join(self.root, ".gitignore")
        if not os.path.exists(gi):
            with open(gi, "w", encoding="utf-8") as f:
                f.write("# Uploaded DfT sources and their registry; large and host-specific, never committed.\n*\n")
        with self.db() as c:
            c.executescript(SCHEMA)
        self._ready = True

    def connect(self):
        c = sqlite3.connect(os.path.join(self.root, "registry.db"), timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        return c

    @contextmanager
    def db(self):
        c = self.connect()
        try:
            yield c
        finally:
            c.close()

    def path(self, *parts):
        p = os.path.abspath(os.path.join(self.root, *parts))
        if os.path.commonpath([p, self.root]) != self.root:
            raise ValueError(f"path escapes the registry: {parts}")
        return p

    def event(self, c, event, job_id=None, version_id=None, detail=None):
        c.execute("INSERT INTO events (at, job_id, version_id, event, detail) VALUES (?,?,?,?,?)",
                  (now(), job_id, version_id, event, json.dumps(detail, default=str) if detail is not None else None))

    # -------------------------------------------------------------- jobs
    def create_job(self, original_filename, declared_bytes):
        self.ensure()
        job_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        with self.db() as c:
            t = now()
            c.execute("INSERT INTO jobs (id, state, stage, original_filename, declared_bytes, created_at, updated_at) "
                      "VALUES (?, 'UPLOADING', 'upload', ?, ?, ?, ?)", (job_id, original_filename, declared_bytes, t, t))
            self.event(c, "upload_started", job_id, detail={"filename": original_filename, "declared_bytes": declared_bytes})
        return job_id

    def update_job(self, job_id, **fields):
        fields["updated_at"] = now()
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self.db() as c:
            c.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))

    def job(self, job_id):
        self.ensure()
        with self.db() as c:
            r = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(r) if r else None

    def jobs(self, limit=50):
        self.ensure()
        with self.db() as c:
            return [dict(r) for r in c.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))]

    def count_jobs(self, states):
        self.ensure()
        with self.db() as c:
            q = "SELECT COUNT(*) FROM jobs WHERE state IN (%s)" % ",".join("?" * len(states))
            return c.execute(q, states).fetchone()[0]

    def finish_job(self, job_id, state, **fields):
        fields.update(state=state, finished_at=now())
        self.update_job(job_id, **fields)
        with self.db() as c:
            self.event(c, f"job_{state.lower()}", job_id, detail={k: v for k, v in fields.items() if k in ("error", "message", "stage")})

    def upload_path(self, job_id, part=False):
        return self.path("incoming", f"{job_id}.{'part' if part else 'upload'}")

    def work_dir(self, job_id):
        return self.path("work", job_id)

    def result(self, job_id):
        p = os.path.join(self.work_dir(job_id), "result.json")
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    def find_by_sha(self, sha):
        """Existing upload or version with identical bytes (for duplicate detection)."""
        with self.db() as c:
            v = c.execute("SELECT * FROM versions WHERE sha256 = ?", (sha,)).fetchone()
            j = c.execute("SELECT id, state FROM jobs WHERE sha256 = ? AND state NOT IN ('FAILED', 'REJECTED') "
                          "ORDER BY created_at LIMIT 1", (sha,)).fetchone()
        return (dict(v) if v else None), (dict(j) if j else None)

    def keep_failed(self, job_id, error, src=None):
        """Preserve a failed upload for diagnosis when it is small enough; always keep the error record."""
        d = self.path("failed", job_id)
        os.makedirs(d, exist_ok=True)
        kept = False
        if src and os.path.exists(src):
            if os.path.getsize(src) <= self.settings.keep_failed_upload_bytes:
                shutil.move(src, os.path.join(d, "original.bin"))
                kept = True
            else:
                os.remove(src)
        with open(os.path.join(d, "error.json"), "w", encoding="utf-8") as f:
            json.dump({"job_id": job_id, "error": error, "original_kept": kept, "at": now()}, f, indent=2)
        return kept

    # -------------------------------------------------------------- recovery
    def recover(self):
        """Called once at start-up: no worker or upload survives a restart."""
        self.ensure()
        fixed = []
        with self.db() as c:
            for r in c.execute("SELECT id, state FROM jobs WHERE state IN (%s)" % ",".join("?" * len(ACTIVE_STATES)), ACTIVE_STATES).fetchall():
                job_id, state = r["id"], r["state"]
                if state == "UPLOADING":
                    part = self.upload_path(job_id, part=True)
                    if os.path.exists(part):
                        os.remove(part)
                    msg = "upload interrupted (application restarted); please upload again"
                else:
                    msg = "processing interrupted (application restarted); use Retry"
                c.execute("UPDATE jobs SET state='FAILED', stage=?, error=?, updated_at=?, finished_at=? WHERE id=?",
                          ("upload" if state == "UPLOADING" else "parse", msg, now(), now(), job_id))
                self.event(c, "job_recovered_as_failed", job_id, detail={"previous_state": state})
                fixed.append(job_id)
            for v in c.execute("SELECT * FROM versions WHERE status = 'PENDING_FILES'").fetchall():
                ok = self._verify_version_dir(dict(v))
                status = "STAGED" if ok else "FAILED_STAGING"
                c.execute("UPDATE versions SET status=? WHERE id=?", (status, v["id"]))
                self.event(c, "version_recovered", version_id=v["id"], detail={"status": status})
                fixed.append(v["label"])
        for name in os.listdir(self.path("incoming")):
            if name.endswith(".part") and not self.job(name[:-5]):
                os.remove(self.path("incoming", name))
        return fixed

    # -------------------------------------------------------------- versions
    def versions(self, dataset=None):
        self.ensure()
        with self.db() as c:
            if dataset:
                rows = c.execute("SELECT * FROM versions WHERE dataset = ? ORDER BY id DESC", (dataset,))
            else:
                rows = c.execute("SELECT * FROM versions ORDER BY dataset, id DESC")
            return [dict(r) for r in rows]

    def version(self, version_id):
        self.ensure()
        with self.db() as c:
            r = c.execute("SELECT * FROM versions WHERE id = ?", (version_id,)).fetchone()
        return dict(r) if r else None

    def events(self, job_id=None, version_id=None, limit=200):
        self.ensure()
        with self.db() as c:
            if job_id:
                rows = c.execute("SELECT * FROM events WHERE job_id = ? ORDER BY id", (job_id,))
            elif version_id:
                rows = c.execute("SELECT * FROM events WHERE version_id = ? ORDER BY id", (version_id,))
            else:
                rows = c.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(r) for r in rows]

    def stage(self, job_id, confirm_dataset, confirm_period, restatement_reason=None):
        """Save a validated upload as an immutable source version. Never touches production."""
        self.ensure()
        job = self.job(job_id)
        if not job:
            raise StagingRefused("unknown upload")
        if job["state"] != "READY":
            raise StagingRefused(f"upload is {job['state']}, only a validated upload can be saved")
        res = self.result(job_id)
        if not res or res.get("status") != "VALID":
            raise StagingRefused("validation did not pass; nothing can be saved")
        if confirm_dataset != res["dataset"] or confirm_period != res["profile"]["latest_quarter"]:
            raise StagingRefused("confirmation does not match the identified dataset and reporting period")
        upload = self.upload_path(job_id)
        if not os.path.exists(upload) or sha256_file(upload) != job["sha256"]:
            raise StagingRefused("uploaded bytes are missing or changed since validation")
        canonical = os.path.join(self.work_dir(job_id), "canonical.csv")
        if sha256_file(canonical) != res["canonical_sha256"]:
            raise StagingRefused("canonical table changed since validation")
        dataset, period = res["dataset"], res["profile"]["latest_quarter"]
        prod = res.get("production") or {}
        if prod.get("sha256") == job["sha256"]:
            raise StagingRefused("identical to the current production source; nothing to stage")

        c = self.connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            dup = c.execute("SELECT label FROM versions WHERE sha256 = ?", (job["sha256"],)).fetchone()
            if dup:
                raise StagingRefused(f"identical file already stored as {dup['label']}")
            prior = [dict(r) for r in c.execute(
                "SELECT * FROM versions WHERE dataset = ? AND status IN ('STAGED', 'PENDING_FILES') ORDER BY id DESC", (dataset,))]
            same_q = [v for v in prior if v["period_label"] == period]
            newer = [v for v in prior if _qidx(v["period_label"]) > _qidx(period)]
            if newer:
                raise StagingRefused(f"{dataset} {newer[0]['period_label']} is already staged ({newer[0]['label']}); "
                                     f"an older reporting period cannot be staged after it")
            prod_q = prod.get("latest_quarter")
            if prod_q and _qidx(period) < _qidx(prod_q):
                raise StagingRefused(f"{period} is older than the current production source ({prod_q})")
            same_as_prod = bool(prod_q) and period == prod_q
            restates = None
            if same_q or same_as_prod:
                if not (restatement_reason and restatement_reason.strip()):
                    what = same_q[0]["label"] if same_q else f"the production source ({prod_q})"
                    raise StagingRefused(f"conflicting reporting period: {dataset} {period} differs from {what}. "
                                         f"If this is a DfT revision of the same quarter, give a restatement reason.")
                restates = same_q[0]["id"] if same_q else None
            n = c.execute("SELECT COUNT(*) FROM versions WHERE dataset = ? AND period_label = ?", (dataset, period)).fetchone()[0]
            label = f"{dataset}-{period.replace(' ', '')}-v{n + 1}"
            rel_dir = os.path.join("versions", dataset, period.replace(" ", "_"), label)
            supersedes = prior[0]["id"] if prior else None
            ext = {"csv": "csv", "xlsx": "xlsx", "ods": "ods"}[job["format"]]
            cur = c.execute(
                "INSERT INTO versions (label, dataset, period_label, period_end, sha256, canonical_sha256, original_filename, "
                "format, sheet, size_bytes, rows, status, supersedes_version_id, restates_version_id, restatement_reason, "
                "production_sha256, production_latest, job_id, path, uploaded_at, staged_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?, 'PENDING_FILES', ?,?,?,?,?,?,?,?,?)",
                (label, dataset, period, res["profile"]["period_end"], job["sha256"], res["canonical_sha256"],
                 job["original_filename"], job["format"], res.get("sheet"), job["received_bytes"], res["profile"]["rows"],
                 supersedes, restates, restatement_reason, prod.get("sha256"), prod_q, job_id, rel_dir.replace("\\", "/"),
                 job["created_at"], now()))
            version_id = cur.lastrowid
            self.event(c, "version_reserved", job_id, version_id, {"label": label})
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK") if c.in_transaction else None
            raise
        finally:
            c.close()

        final = self.path(rel_dir)
        tmp = final + ".tmp"
        try:
            os.makedirs(tmp, exist_ok=False)
            shutil.copyfile(upload, os.path.join(tmp, f"original.{ext}"))
            shutil.copyfile(canonical, os.path.join(tmp, "canonical.csv"))
            with open(os.path.join(tmp, "profile.json"), "w", encoding="utf-8") as f:
                json.dump({k: res[k] for k in ("profile", "checks", "comparison", "identification", "production", "flags") if k in res},
                          f, indent=2, default=str)
            manifest = {
                "label": label, "version_id": version_id, "dataset": dataset, "reporting_period": period,
                "period_end": res["profile"]["period_end"], "original_filename": job["original_filename"],
                "format": job["format"], "sheet": res.get("sheet"), "size_bytes": job["received_bytes"],
                "uploaded_at": job["created_at"], "staged_at": now(), "rows": res["profile"]["rows"],
                "supersedes_version_id": supersedes, "restates_version_id": restates, "restatement_reason": restatement_reason,
                "production_source_at_staging": prod, "status": "STAGED (candidate source; not approved for publication)",
                "files": {}}
            for name in (f"original.{ext}", "canonical.csv", "profile.json"):
                manifest["files"][name] = {"sha256": sha256_file(os.path.join(tmp, name)),
                                           "bytes": os.path.getsize(os.path.join(tmp, name))}
            if manifest["files"][f"original.{ext}"]["sha256"] != job["sha256"]:
                raise IOError("original bytes changed while copying")
            with open(os.path.join(tmp, "manifest.json"), "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2, default=str)
            _replace_retry(tmp, final)
            for name in os.listdir(final):
                os.chmod(os.path.join(final, name), stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
            msha = sha256_file(os.path.join(final, "manifest.json"))
            with self.db() as c2:
                c2.execute("UPDATE versions SET status='STAGED', manifest_sha256=? WHERE id=?", (msha, version_id))
                c2.execute("UPDATE jobs SET state='STAGED', version_id=?, updated_at=?, finished_at=? WHERE id=?",
                           (version_id, now(), now(), job_id))
                self.event(c2, "version_staged", job_id, version_id, {"label": label, "supersedes": supersedes, "restates": restates})
            os.remove(upload)
            shutil.rmtree(self.work_dir(job_id), ignore_errors=True)
        except Exception as e:
            with self.db() as c3:
                c3.execute("UPDATE versions SET status='FAILED_STAGING' WHERE id=?", (version_id,))
                self.event(c3, "version_staging_failed", job_id, version_id, {"error": f"{type(e).__name__}: {e}"})
            raise
        return self.version(version_id)

    def _verify_version_dir(self, v):
        d = self.path(v["path"])
        try:
            with open(os.path.join(d, "manifest.json"), encoding="utf-8") as f:
                m = json.load(f)
            return all(sha256_file(os.path.join(d, n)) == info["sha256"] for n, info in m["files"].items()) and m["version_id"] == v["id"]
        except (OSError, ValueError, KeyError):
            return False

    def verify_version(self, version_id):
        v = self.version(version_id)
        return bool(v) and v["status"] == "STAGED" and self._verify_version_dir(v) and \
            sha256_file(os.path.join(self.path(v["path"]), "manifest.json")) == v["manifest_sha256"]

    def reject(self, job_id, reason):
        job = self.job(job_id)
        if not job:
            raise StagingRefused("unknown upload")
        if job["state"] in ACTIVE_STATES or job["state"] == "STAGED":
            raise StagingRefused(f"upload is {job['state']} and cannot be rejected")
        up = self.upload_path(job_id)
        res = self.result(job_id) or {}
        if res.get("status") == "ERROR" and os.path.exists(up):
            # A parser failure on a well-formed container: keep the bytes (size-limited) for diagnosis.
            self.keep_failed(job_id, res.get("error"), up)
        elif os.path.exists(up):
            os.remove(up)
        shutil.rmtree(self.work_dir(job_id), ignore_errors=True)
        self.finish_job(job_id, "REJECTED", message=reason or "rejected by reviewer")


def _replace_retry(src, dst, attempts=12, delay=0.05):
    """os.replace with a short retry: on Windows a scanner or indexer briefly holding freshly written files makes the
    rename fail with 'Access is denied' (same remedy as the promotion tool). A persistent failure is re-raised."""
    import time
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(delay * (i + 1))


def _qidx(label):
    y, q = label.split(" Q")
    return int(y) * 4 + int(q) - 1
