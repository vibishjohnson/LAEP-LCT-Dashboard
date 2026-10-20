"""
Publish a validated SMMT nowcast candidate into its own versioned integration files (never C1 actuals or forecasts).

Published layout (project/output_processed/integration/):
    smmt_nowcast_dno.csv, smmt_nowcast_la.csv, smmt_nowcast_laep.csv   - byte copies of the candidate's files
    smmt_nowcast_manifest.json                                         - candidate identity, baseline, months, file hashes
The manifest is written last and removed first, so a reader that finds a manifest whose hashes match finds a complete,
consistent publication. The dashboard shows the nowcast only when the manifest baseline equals the live
latest_actual_period and every file matches its hash; otherwise it falls back to the charts without the nowcast.

Only a VALIDATED, non-stale candidate whose baseline is the live latest actual can be published, with an exact approval
token. The live repository is refused unless publication is explicitly enabled for it (allow_live) AND the C1 live-release
switch is on; this module is exercised in disposable environments until that approval is given.
"""

import hashlib
import json
import os
import shutil
import sys
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional

from .smmt_candidate_builder import INTEGRATION_REL, METHOD_ID, PUBLISHED_FILES, CandidateError, SMmtCandidateBuilder

MANIFEST = "smmt_nowcast_manifest.json"
BACKUP_REL = "project/dft_staging/smmt_publish_backups"
LOG_REL = "project/dft_staging/smmt_publish_log.jsonl"


class PublishRefused(Exception):
    pass


def _fault(root: str, stage: str):
    """Test-only interruption points (LCT_SMMT_FAULT="<stage>:raise|exit"); never active on the live repository."""
    if _is_live(root):
        return
    for item in filter(None, os.environ.get("LCT_SMMT_FAULT", "").split(",")):
        st, _, mode = item.partition(":")
        if st == stage and mode == "raise":
            raise PublishRefused(f"injected failure at {stage}")
        if st == stage and mode == "exit":
            os._exit(70)


def _sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_manifest(builder: SMmtCandidateBuilder, cid: str) -> Dict:
    """The publication manifest for a candidate (the candidate preview serves the same one)."""
    return builder.publication_manifest(cid)


def _integ(root: str) -> str:
    return os.path.join(root, *INTEGRATION_REL.split("/"))


def _is_live(root: str) -> bool:
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.normcase(os.path.realpath(root)) == os.path.normcase(os.path.realpath(repo))


def _guard_live(root: str, allow_live: bool):
    if _is_live(root):
        sys.path.insert(0, os.path.join(root, "experiments", "ev_redistribution_c1", "release"))
        import release_config as cfg   # noqa: E402
        if not (allow_live and cfg.LIVE_RELEASE_ENABLED):
            raise PublishRefused("publishing to the live repository needs explicit approval (allow_live) and "
                                 "LIVE_RELEASE_ENABLED=True")


def current(root: str) -> Optional[Dict]:
    p = os.path.join(_integ(root), MANIFEST)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def plan(root: str, cid: str, db_path: Optional[str] = None) -> Dict:
    b = SMmtCandidateBuilder(root, db_path)
    st = b.status(cid)
    man = b.load(cid)
    meta = json.load(open(os.path.join(_integ(root), "metadata.json"), encoding="utf-8"))
    problems = []
    if st["state"] != "VALIDATED":
        problems.append(f"candidate is {st['state']}: {st['detail']}")
    if man.get("method_id") != METHOD_ID:
        problems.append(f"candidate method {man.get('method_id')} is not the approved {METHOD_ID}")
    if man.get("baseline_period") != meta.get("latest_actual_period"):
        problems.append(f"candidate baseline {man.get('baseline_period')} is not the live latest actual {meta.get('latest_actual_period')}")
    seal = man.get("seal_sha256", "")
    return {"root": root, "candidate_id": cid, "valid": not problems, "problems": problems,
            "approval_token": f"PUBLISH SMMT {cid} {seal[:16]}", "previous": current(root)}


def publish(root: str, cid: str, approve: str, db_path: Optional[str] = None, allow_live: bool = False) -> Dict:
    _guard_live(root, allow_live)
    p = plan(root, cid, db_path)
    if not p["valid"]:
        raise PublishRefused("; ".join(p["problems"]))
    if approve != p["approval_token"]:
        raise PublishRefused(f"approval must be exactly: {p['approval_token']}")
    b = SMmtCandidateBuilder(root, db_path)
    manifest = build_manifest(b, cid)
    integ = _integ(root)
    protected = {n: _sha(os.path.join(integ, n)) for n in sorted(os.listdir(integ))
                 if n not in PUBLISHED_FILES + (MANIFEST,) and os.path.isfile(os.path.join(integ, n))}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    backup = os.path.join(root, *BACKUP_REL.split("/"), stamp)
    os.makedirs(backup)
    for n in PUBLISHED_FILES + (MANIFEST,):
        if os.path.exists(os.path.join(integ, n)):
            shutil.copy2(os.path.join(integ, n), os.path.join(backup, n))
    # withdraw the old manifest first, then replace the data files, then write the new manifest last
    if os.path.exists(os.path.join(integ, MANIFEST)):
        os.remove(os.path.join(integ, MANIFEST))
    _fault(root, "after_manifest_removed")
    for i, n in enumerate(PUBLISHED_FILES):
        tmp = os.path.join(integ, f".{n}.{uuid.uuid4().hex[:6]}.tmp")
        shutil.copyfile(os.path.join(b.folder(cid), n), tmp)
        if _sha(tmp) != manifest["files"][n]:
            os.remove(tmp)
            raise PublishRefused(f"copy of {n} does not match the candidate")
        os.replace(tmp, os.path.join(integ, n))
        _fault(root, f"after_file_{i}")
    manifest.update({"published_at": _now(), "previous_candidate_id": (p["previous"] or {}).get("candidate_id"),
                     "backup": os.path.relpath(backup, root).replace("\\", "/")})
    _fault(root, "before_manifest")
    tmp = os.path.join(integ, f".{MANIFEST}.{uuid.uuid4().hex[:6]}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=1)
    os.replace(tmp, os.path.join(integ, MANIFEST))
    bad = [n for n in PUBLISHED_FILES if _sha(os.path.join(integ, n)) != manifest["files"][n]]
    changed = [n for n, h in protected.items() if _sha(os.path.join(integ, n)) != h]
    if bad or changed:
        raise PublishRefused(f"post-publication check failed: mismatched {bad}, other integration files changed {changed}")
    _log(root, {"action": "publish", "candidate_id": cid, "at": manifest["published_at"], "backup": manifest["backup"]})
    return manifest


def withdraw(root: str, approve: str, allow_live: bool = False) -> Dict:
    """Remove the published nowcast (manifest first). The dashboard then shows its charts without the nowcast."""
    _guard_live(root, allow_live)
    cur = current(root)
    integ = _integ(root)
    leftovers = [n for n in os.listdir(integ) if n in PUBLISHED_FILES or (n.startswith(".smmt_nowcast") and n.endswith(".tmp"))]
    if not cur and not leftovers:
        raise PublishRefused("no SMMT nowcast is published")
    # an interrupted first publication leaves files without a manifest (never displayed); they can be cleaned up
    token = f"WITHDRAW SMMT {cur['candidate_id']}" if cur else "WITHDRAW SMMT PARTIAL"
    if approve != token:
        raise PublishRefused(f"approval must be exactly: {token}")
    for n in os.listdir(integ):
        if n.startswith(".smmt_nowcast") and n.endswith(".tmp"):
            os.remove(os.path.join(integ, n))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    backup = os.path.join(root, *BACKUP_REL.split("/"), stamp)
    os.makedirs(backup)
    for n in (MANIFEST,) + PUBLISHED_FILES:
        if os.path.exists(os.path.join(integ, n)):
            shutil.copy2(os.path.join(integ, n), os.path.join(backup, n))
            os.remove(os.path.join(integ, n))
    rec = {"action": "withdraw", "candidate_id": cur["candidate_id"] if cur else None, "partial": not cur, "at": _now(),
           "backup": os.path.relpath(backup, root).replace("\\", "/")}
    _log(root, rec)
    return rec


def _log(root: str, rec: Dict):
    p = os.path.join(root, *LOG_REL.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def status(root: str) -> Dict:
    """What the dashboard would do: show (current), hide (stale/partial/altered) or nothing published."""
    integ = _integ(root)
    cur = current(root)
    meta = json.load(open(os.path.join(integ, "metadata.json"), encoding="utf-8"))
    files = {n: os.path.exists(os.path.join(integ, n)) for n in PUBLISHED_FILES}
    if not cur:
        return {"state": "PARTIAL" if any(files.values()) else "NONE", "files_present": files}
    bad = [n for n in PUBLISHED_FILES if not files[n] or _sha(os.path.join(integ, n)) != cur["files"][n]]
    state = "ALTERED" if bad else "STALE" if cur["baseline_period"] != meta.get("latest_actual_period") else "CURRENT"
    return {"state": state, "candidate_id": cur["candidate_id"], "baseline_period": cur["baseline_period"],
            "latest_actual_period": meta.get("latest_actual_period"), "mismatched_files": bad}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="SMMT nowcast publication (status | plan | publish | withdraw)")
    ap.add_argument("operation", choices=["status", "plan", "publish", "withdraw"])
    ap.add_argument("--root", required=True)
    ap.add_argument("--candidate")
    ap.add_argument("--approve", default="")
    ap.add_argument("--allow-live", action="store_true")
    a = ap.parse_args()
    try:
        if a.operation == "status":
            out = status(a.root)
        elif a.operation == "plan":
            out = plan(a.root, a.candidate)
        elif a.operation == "publish":
            out = publish(a.root, a.candidate, a.approve, allow_live=a.allow_live)
        else:
            out = withdraw(a.root, a.approve, allow_live=a.allow_live)
        print(json.dumps(out, indent=1, default=str))
    except (PublishRefused, CandidateError) as e:
        print(json.dumps({"error": type(e).__name__, "message": str(e)}, indent=1))
        sys.exit(2)
