"""
C1 refresh command line (also the background worker entry point).

  python -m data_manager.c1_refresh.cli register <VEH0135 file>     validate + stage into the isolated candidate registry
  python -m data_manager.c1_refresh.cli baseline                    source set of the live release + byte-for-byte reproduction
  python -m data_manager.c1_refresh.cli generate <version label>    source set + sealed candidate
  python -m data_manager.c1_refresh.cli verify <candidate id>
  python -m data_manager.c1_refresh.cli dry-run <candidate id>                disposable release dry run (option B package)
  python -m data_manager.c1_refresh.cli archive <candidate id>                durable archive (experiments/.../quarterly_archive)
  python -m data_manager.c1_refresh.cli verify-archive <archive dir>
  python -m data_manager.c1_refresh.cli restore-workspace <archive dir> <new workspace dir>
  python -m data_manager.c1_refresh.cli reproduce <archive dir>               regenerate from archived inputs and compare

Every command writes only inside the C1 workspace (LCT_C1_WORKSPACE, default <repo>/c1_candidates).
"""

import json
import os
import shutil
import sys
from dataclasses import replace

from . import config as C
from . import engine, sources

RELEASE_SOURCE = os.path.join(C.PROJECT_ROOT, "ev", "df_VEH0135.csv")      # read-only: the bytes the live release used


def status(ws, job, **fields):
    if not job:
        return
    p = ws.path("jobs", f"{job}.json")
    cur = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    cur.update(fields)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(cur, f, indent=1, default=str)
    os.replace(p + ".tmp", p)


def register(ws, path):
    """Run the Phase 12 ingestion pipeline (identify, validate, compare, stage) into the isolated registry."""
    from data_manager.ingestion.config import Settings
    from data_manager.ingestion.registry import Registry, sha256_file
    from data_manager.ingestion import worker
    ws.ensure()
    s = replace(Settings(), registry_root=ws.registry_root, compare_with_production=True)
    reg = Registry(s)
    reg.ensure()
    sha = sha256_file(path)
    existing = next((v for v in reg.versions("VEH0135") if v["sha256"] == sha), None)
    if existing:
        return {"reused": True, **existing}
    job = reg.create_job(os.path.basename(path), os.path.getsize(path))
    shutil.copyfile(path, reg.upload_path(job))
    reg.update_job(job, received_bytes=os.path.getsize(path), sha256=sha)
    worker.run(job, s)
    j = reg.job(job)
    res = reg.result(job)
    if j["state"] != "READY" or res["dataset"] != "VEH0135":
        raise SystemExit(f"source did not validate as VEH0135: {j['state']} {j.get('error')}")
    v = reg.stage(job, "VEH0135", res["profile"]["latest_quarter"])
    return {"reused": False, **v, "validation": {c["check"]: c["status"] for c in res["checks"]}, "flags": res["flags"]}


def baseline(ws):
    ws.ensure()
    sm = sources.from_release_source(ws, RELEASE_SOURCE)
    return engine.reproduce_baseline(ws, sm["source_set_id"])


def release_set_id(ws):
    sets = [s for s in sources.list_sets(ws) if s["kind"] == "RELEASE_SOURCE" and s["live_release"]["release_id"] == C.LIVE_RELEASE_ID]
    if not sets:
        raise SystemExit("run the baseline reproduction first")
    return sets[0]["source_set_id"]


def generate(ws, label, job=None):
    status(ws, job, state="RUNNING", stage="source set", started=sources.now())
    sm = sources.from_registered_version(ws, label)
    status(ws, job, stage="candidate", source_set_id=sm["source_set_id"])
    man = engine.build_candidate(ws, sm["source_set_id"], release_set_id(ws), progress=lambda s: status(ws, job, stage=s))
    status(ws, job, state="COMPLETE", stage="done", candidate_id=man["candidate_id"], finished=sources.now(), resources=man["resources"],
           timings=man["timings_s"])
    return man


def main(argv):
    ws = C.Workspace()
    cmd = argv[1]
    job = os.environ.get("LCT_C1_JOB")
    try:
        if cmd == "register":
            out = register(ws, argv[2])
        elif cmd == "baseline":
            out = baseline(ws)
        elif cmd == "generate":
            out = generate(ws, argv[2], job)
            out = {k: out[k] for k in ("candidate_id", "qa", "restatements_detected", "timings_s", "resources", "publication_options")}
        elif cmd == "verify":
            out = engine.verify_package(ws, argv[2])
        elif cmd in ("archive", "verify-archive", "restore-workspace", "reproduce"):
            from . import archive
            if cmd == "archive":
                out = archive.archive_candidate(ws, argv[2])
            elif cmd == "verify-archive":
                out = archive.verify_archive(argv[2])
            elif cmd == "restore-workspace":
                out = {"workspace": archive.restore_workspace(argv[2], argv[3]).root}
            else:
                out = archive.reproduce(argv[2])
        elif cmd == "dry-run":
            from . import release_readiness
            status(ws, job, state="RUNNING", stage="dry run", started=sources.now(), candidate_id=argv[2])
            r = release_readiness.dry_run(ws, argv[2], progress=lambda s: status(ws, job, stage=s))
            status(ws, job, state="COMPLETE" if r["pass"] else "FAILED", stage="done", finished=sources.now(),
                   error=None if r["pass"] else "dry run failed (see report)", dry_run_pass=r["pass"])
            out = {k: r[k] for k in ("pass", "release_id", "seconds", "memory_mb")}
        else:
            raise SystemExit(__doc__)
    except Exception as e:  # noqa: BLE001 - recorded for the UI, then re-raised
        status(ws, job, state="FAILED", error=f"{type(e).__name__}: {e}", finished=sources.now())
        raise
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
