"""
Immutable C1 source sets.

A source set names exactly one VEH0135 byte stream (by SHA-256), the lookups C1 genuinely reads,
the approved methodology code (by hash), the live release it is compared with, and the historical
source vintage that release used. It is written once, sealed, made read-only and never changed.

Two kinds:
  REGISTERED_VERSION  a VEH0135 version staged in the candidate source registry (ingestion pipeline:
                      identified, validated, immutable original bytes)
  RELEASE_SOURCE      the exact bytes an existing release was built from (for baseline reproduction);
                      copied once into the source set and checked against the release provenance
There is no fallback: a missing, changed or unvalidated source is an error, and ev/ is never used as
a substitute for a registered version.
"""

import datetime as dt
import hashlib
import json
import os
import shutil
import stat
import subprocess

from . import config as C
from data_manager.ingestion.config import Settings
from data_manager.ingestion.registry import Registry


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def seal(obj):
    body = {k: v for k, v in obj.items() if k != "seal_sha256"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def git_head():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=C.PROJECT_ROOT, capture_output=True, text=True, timeout=30).stdout.strip()
    except OSError:
        return None


def code_revision():
    """Approved method code and this package, by content hash (uncommitted files are therefore still pinned)."""
    files = list(C.METHOD_CODE_REL)
    pkg = os.path.join(C.PROJECT_ROOT, "data_manager", "c1_refresh")
    files += [f"data_manager/c1_refresh/{n}" for n in sorted(os.listdir(pkg)) if n.endswith(".py")]
    dirty = subprocess.run(["git", "status", "--porcelain", "--", *files], cwd=C.PROJECT_ROOT, capture_output=True, text=True).stdout
    return {"git_head": git_head(), "files_sha256": {f: sha256_file(os.path.join(C.PROJECT_ROOT, f)) for f in files},
            "uncommitted_files": sorted(l[3:].strip() for l in dirty.splitlines())}


def lookup_hashes():
    return {k: {"path": rel, "sha256": sha256_file(os.path.join(C.PROJECT_ROOT, rel))} for k, rel in C.LOOKUPS_REL.items()}


def live_release_reference():
    import c1_release as rel
    m = rel.load_manifest(C.PROJECT_ROOT, C.LIVE_RELEASE_ID)          # verifies the manifest seal
    targets = {n: t["new_sha256"] for n, t in m["targets"].items()}
    live = {n: sha256_file(os.path.join(C.PROJECT_ROOT, C.LIVE_INTEGRATION_REL, n)) for n in C.INTEGRATION_NAMES}
    if live != targets or m["status"] != "COMPLETE":
        raise ValueError("live EV actuals do not match the sealed release; refusing to build a source set")
    return {"release_id": C.LIVE_RELEASE_ID, "status": m["status"], "manifest_sha256": m["manifest_sha256"], "target_sha256": targets}


def _release_source_vintage():
    """The VEH0135 vintage the live release was built from (shadow manifest provenance)."""
    sm = json.load(open(os.path.join(C.C1_DIR, "shadow_integration", "manifest.json"), encoding="utf-8"))
    return {"source_veh0135_sha256_prefix": sm["candidate_provenance"]["source_veh0135_sha256"], "periods": sm["periods"],
            "source_file_at_release": "ev/df_VEH0135.csv", "release_id": C.LIVE_RELEASE_ID}


def _write_sealed(ws, set_id, manifest):
    d = ws.path("source_sets", set_id)
    path = os.path.join(d, "manifest.json")
    manifest["seal_sha256"] = seal(manifest)
    data = json.dumps(manifest, indent=2, sort_keys=True, default=str).encode("utf-8")
    if os.path.exists(path):
        if open(path, "rb").read() != data:
            existing = json.load(open(path, encoding="utf-8"))
            if existing.get("source", {}).get("sha256") != manifest["source"]["sha256"]:
                raise ValueError(f"source set {set_id} already exists with different source bytes")
        return load(ws, set_id)                      # immutable: an existing set is reused, never rewritten
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    open(tmp, "wb").write(data)
    os.replace(tmp, path)
    for n in os.listdir(d):
        os.chmod(os.path.join(d, n), stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
    return load(ws, set_id)


def from_registered_version(ws, version_label):
    """Source set for a staged VEH0135 version in the candidate source registry."""
    ws.ensure()
    reg = Registry(Settings(registry_root=ws.registry_root))
    v = next((x for x in reg.versions("VEH0135") if x["label"] == version_label), None)
    if not v:
        raise ValueError(f"no registered VEH0135 version {version_label!r} in {ws.registry_root}")
    if v["status"] != "STAGED" or not reg.verify_version(v["id"]):
        raise ValueError(f"{version_label} is not an intact staged version")
    vdir = reg.path(v["path"])
    reg_manifest = json.load(open(os.path.join(vdir, "manifest.json"), encoding="utf-8"))
    profile = json.load(open(os.path.join(vdir, "profile.json"), encoding="utf-8"))
    original = os.path.join(vdir, "original.csv")
    if sha256_file(original) != v["sha256"]:
        raise ValueError("registered bytes do not match their recorded SHA-256")
    failed = [c for c in profile["checks"] if c["status"] == "FAIL"]
    if failed:
        raise ValueError(f"source failed validation: {failed}")
    quarters = C.quarters_through(v["period_label"])
    if not set(quarters) <= set(profile["profile"]["quarters"]):
        raise ValueError(f"source lacks quarters {sorted(set(quarters) - set(profile['profile']['quarters']))}")
    set_id = f"SS-VEH0135-{v['period_label'].replace(' ', '')}-{v['sha256'][:12]}"
    manifest = {
        "schema": C.SOURCE_SET_SCHEMA, "source_set_id": set_id, "kind": "REGISTERED_VERSION", "created_at": now(),
        "dataset": "VEH0135", "reporting_quarter": v["period_label"], "latest_period": C.quarter_period(v["period_label"]),
        "quarters": quarters, "periods": [C.quarter_period(q) for q in quarters],
        "source": {"path": os.path.relpath(original, ws.root).replace("\\", "/"), "path_base": "workspace",
                   "sha256": v["sha256"], "size_bytes": v["size_bytes"], "original_filename": v["original_filename"],
                   "registry_version_label": v["label"], "registry_version_id": v["id"],
                   "registry_manifest_sha256": v["manifest_sha256"], "uploaded_at": v["uploaded_at"], "staged_at": v["staged_at"],
                   "dft_publication": "DfT vehicle licensing statistics, table VEH0135 data file (df_VEH0135.csv), gov.uk",
                   "validation": {"schema_contract": "data_manager.ingestion.contracts VEH0135", "status": "VALID",
                                  "checks": {c["check"]: c["status"] for c in profile["checks"]},
                                  "rows": profile["profile"]["rows"], "scope": profile["profile"]["scope"]}},
        "lookups": lookup_hashes(),
        "methodology": {"methodology_id": "C1_DYNAMIC_PRIVATE_EV", "lpn_min_share": 0.20, "history_start": C.FIRST_QUARTER,
                        "window": "EXPANDING (all quarters from March 2025; never rolling)", "code": code_revision()},
        "live_release": live_release_reference(),
        "historical_vintages": [_release_source_vintage()],
        "status": "SOURCE_VALIDATED (candidate input; not an approved production source)",
    }
    return _write_sealed(ws, set_id, manifest)


def from_release_source(ws, release_source_path):
    """Source set for the exact bytes the live release used (baseline reproduction)."""
    ws.ensure()
    vintage = _release_source_vintage()
    sha = sha256_file(release_source_path)
    if not sha.startswith(vintage["source_veh0135_sha256_prefix"]):
        raise ValueError(f"{release_source_path} is not the source the live release was built from "
                         f"({sha[:16]} != {vintage['source_veh0135_sha256_prefix']})")
    set_id = f"SS-VEH0135-RELEASE-{C.LIVE_RELEASE_ID}-{sha[:12]}"
    d = ws.path("source_sets", set_id)
    os.makedirs(d, exist_ok=True)
    copy = os.path.join(d, "VEH0135.csv")
    if not os.path.exists(copy):
        shutil.copyfile(release_source_path, copy + ".tmp")
        os.replace(copy + ".tmp", copy)
    if sha256_file(copy) != sha:
        raise ValueError("copied release source does not match")
    quarters = [q for q in C.quarters_through("2026 Q1")]
    manifest = {
        "schema": C.SOURCE_SET_SCHEMA, "source_set_id": set_id, "kind": "RELEASE_SOURCE", "created_at": now(),
        "dataset": "VEH0135", "reporting_quarter": "2026 Q1", "latest_period": "2026-03", "quarters": quarters,
        "periods": [C.quarter_period(q) for q in quarters],
        "source": {"path": os.path.relpath(copy, ws.root).replace("\\", "/"), "path_base": "workspace", "sha256": sha,
                   "size_bytes": os.path.getsize(copy), "original_filename": "df_VEH0135.csv",
                   "copied_from": os.path.relpath(release_source_path, C.PROJECT_ROOT).replace("\\", "/"),
                   "release_provenance_prefix": vintage["source_veh0135_sha256_prefix"]},
        "lookups": lookup_hashes(),
        "methodology": {"methodology_id": "C1_DYNAMIC_PRIVATE_EV", "lpn_min_share": 0.20, "history_start": C.FIRST_QUARTER,
                        "window": "the five quarters of the live release", "code": code_revision()},
        "live_release": live_release_reference(),
        "historical_vintages": [vintage],
        "status": "RELEASE_SOURCE (bytes of the live release input, for reproduction only)",
    }
    return _write_sealed(ws, set_id, manifest)


def load(ws, set_id):
    path = ws.path("source_sets", set_id, "manifest.json")
    m = json.load(open(path, encoding="utf-8"))
    if m.get("seal_sha256") != seal(m):
        raise ValueError(f"source set {set_id} manifest seal is invalid")
    return m


def verified_source_path(ws, m):
    """Absolute path of the source bytes, after re-checking their SHA-256 and the lookups'."""
    p = ws.path(*m["source"]["path"].split("/"))
    if sha256_file(p) != m["source"]["sha256"]:
        raise ValueError(f"source bytes for {m['source_set_id']} changed")
    for k, v in m["lookups"].items():
        if sha256_file(os.path.join(C.PROJECT_ROOT, v["path"])) != v["sha256"]:
            raise ValueError(f"lookup {v['path']} changed since source set {m['source_set_id']}")
    return p


def list_sets(ws):
    d = ws.path("source_sets")
    out = []
    for n in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        try:
            out.append(load(ws, n))
        except (OSError, ValueError):
            continue
    return out
