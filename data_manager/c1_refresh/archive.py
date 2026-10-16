"""
Durable, version-controlled archive of an approved C1 candidate and its inputs (Git LFS for the CSVs).

  archive_candidate   copy the sealed candidate, its source bytes, source set, decision log, release package and the
                      October baseline reproduction into experiments/ev_redistribution_c1/quarterly_archive/<candidate>/
  verify_archive      re-verify every archived byte against the archive manifest and the sealed candidate/package
  restore_workspace   rebuild a C1 workspace from the archive alone (e.g. in a clean checkout)
  reproduce           regenerate the candidate from the archived source with the current code and compare outputs

Excluded on purpose: dry-run reports and job files (contain temporary paths), the ingestion database, operator/host
details and anything not covered by a seal. All writes go to the archive directory or a caller-supplied workspace.
"""

import json
import os
import shutil
import stat
import tempfile

import pandas as pd

from . import config as C
from . import sources as S

import c1_quarterly_release as cq      # noqa: E402

ARCHIVE_ROOT = os.path.join(C.C1_DIR, "quarterly_archive")
SCHEMA = "c1-candidate-archive/1"
GITATTRIBUTES = ("# Archived C1 release inputs: byte-exact. CSVs in Git LFS, everything else without line-ending conversion.\n"
                 "* -text\n*.csv filter=lfs diff=lfs merge=lfs -text\n")
DATA_OUTPUTS = ("integration/append_only/ev_actuals_dno.csv", "integration/append_only/ev_actuals_la.csv",
                "integration/append_only/ev_actuals_laep.csv", "integration/restated/ev_actuals_dno.csv",
                "integration/restated/ev_actuals_la.csv", "integration/restated/ev_actuals_laep.csv",
                "reports/restatement_geography.csv", "reports/restatement_lsoa_changes.csv", "reports/source_revisions.csv",
                "reports/national_pools_and_lpn_floor.csv", "reports/geography_aggregates.csv")
PROVENANCE_COLS = ["source_veh0135_sha256", "source_lsoa_dno_sha256", "code_git_head", "run_utc"]


def archive_dir(cid, root=ARCHIVE_ROOT):
    return os.path.join(root, cid)


def _copy(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    if S.sha256_file(src) != S.sha256_file(dst):
        raise IOError(f"copy mismatch {src}")


def archive_candidate(ws, cid, root=ARCHIVE_ROOT):
    """Write the archive (refuses to overwrite an existing, different archive)."""
    from . import release_readiness
    man = json.load(open(ws.path("candidates", cid, "manifest.json"), encoding="utf-8"))
    from .engine import verify_package
    ok, detail = verify_package(ws, cid)
    if not ok:
        raise ValueError(f"candidate seal check failed: {detail}")
    ss = S.load(ws, man["source_set_id"])
    rs = S.load(ws, man["baseline_source_set_id"])
    src = S.verified_source_path(ws, ss)
    pkg = release_readiness.ensure_package(ws, cid)
    out = archive_dir(cid, root)
    if os.path.exists(os.path.join(out, "ARCHIVE_MANIFEST.json")):
        return verify_archive(out)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, ".gitattributes"), "w", encoding="utf-8", newline="\n") as f:
        f.write(GITATTRIBUTES)
    files = {}

    def put(rel, src_path):
        _copy(src_path, os.path.join(out, *rel.split("/")))
        files[rel] = S.sha256_file(os.path.join(out, *rel.split("/")))

    reg_version = os.path.dirname(src)
    put("source/VEH0135_2026Q2_original.csv", src)
    put("source/registry_version_manifest.json", os.path.join(reg_version, "manifest.json"))
    put("source/registry_version_profile.json", os.path.join(reg_version, "profile.json"))
    put("source/source_set_manifest.json", ws.path("source_sets", ss["source_set_id"], "manifest.json"))
    put("baseline/release_source_set_manifest.json", ws.path("source_sets", rs["source_set_id"], "manifest.json"))
    put("baseline/reproduction.json", ws.path("baseline", rs["source_set_id"], "reproduction.json"))
    cdir = ws.path("candidates", cid)
    for rel in list(man["files_sha256"]) + ["manifest.json"]:
        put(f"candidate/{rel}", os.path.join(cdir, *rel.split("/")))
    put("decision/decisions.jsonl", ws.path("candidates", f"{cid}.decisions.jsonl"))
    put("decision/state.json", ws.path("candidates", f"{cid}.state.json"))
    pdir = release_readiness.package_dir(ws, cid)
    for rel in list(pkg["files_sha256"]) + ["package_manifest.json"]:
        put(f"package/{rel}", os.path.join(pdir, *rel.split("/")))
    am = {"schema": SCHEMA, "candidate_id": cid, "created_at": S.now(), "candidate_seal_sha256": man["seal_sha256"],
          "source_set_id": ss["source_set_id"], "source_sha256": ss["source"]["sha256"], "source_bytes": ss["source"]["size_bytes"],
          "baseline_source_set_id": rs["source_set_id"], "baseline_source_sha256": rs["source"]["sha256"],
          "baseline_source_in_repository": rs["source"]["copied_from"],
          "package_id": pkg["package_id"], "package_seal_sha256": pkg["seal_sha256"],
          "decision_record_sha256": pkg["decision_record_sha256"], "restatement_report_sha256": pkg["restatement_report_sha256"],
          "storage": "Git LFS (CSV) + plain git (JSON/MD); durable once pushed to the governed remote",
          "files_sha256": files}
    am["seal_sha256"] = S.seal(am)
    with open(os.path.join(out, "ARCHIVE_MANIFEST.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(am, f, indent=2, sort_keys=True)
    return verify_archive(out)


def verify_archive(adir):
    am = json.load(open(os.path.join(adir, "ARCHIVE_MANIFEST.json"), encoding="utf-8"))
    problems = []
    if am.get("seal_sha256") != S.seal(am):
        problems.append("archive manifest seal invalid")
    for rel, h in am["files_sha256"].items():
        p = os.path.join(adir, *rel.split("/"))
        if not os.path.isfile(p) or S.sha256_file(p) != h:
            problems.append(f"changed or missing: {rel}")
    man = json.load(open(os.path.join(adir, "candidate", "manifest.json"), encoding="utf-8"))
    if man.get("seal_sha256") != S.seal(man) or man["seal_sha256"] != am["candidate_seal_sha256"]:
        problems.append("candidate manifest seal invalid")
    for rel, h in man["files_sha256"].items():
        if am["files_sha256"].get(f"candidate/{rel}") != h:
            problems.append(f"candidate file {rel} differs from its seal")
    if am["files_sha256"].get("source/VEH0135_2026Q2_original.csv") != am["source_sha256"] or man["input_sha256"]["VEH0135"] != am["source_sha256"]:
        problems.append("source bytes do not match the candidate input hash")
    try:
        pkg = cq.load_package(os.path.join(adir, "package"))
        if pkg["seal_sha256"] != am["package_seal_sha256"] or pkg["candidate_seal_sha256"] != man["seal_sha256"]:
            problems.append("package does not belong to this candidate")
    except Exception as e:  # noqa: BLE001
        problems.append(f"package: {e}")
    prev = None
    for line in open(os.path.join(adir, "decision", "decisions.jsonl"), encoding="utf-8"):
        if not line.strip():
            continue
        d = json.loads(line)
        if d["previous_record_sha256"] != prev or d["record_sha256"] != cq.decision_record_hash(d):
            problems.append("decision log chain broken")
        prev = d["record_sha256"]
    if prev != am["decision_record_sha256"]:
        problems.append("latest decision is not the packaged decision")
    return {"ok": not problems, "problems": problems, "candidate_id": am["candidate_id"], "files": len(am["files_sha256"]),
            "seal_sha256": am["seal_sha256"]}


def restore_workspace(adir, ws_root):
    """A working C1 workspace from the archive and the repository's own October source (ev/df_VEH0135.csv)."""
    v = verify_archive(adir)
    if not v["ok"]:
        raise ValueError(f"archive verification failed: {v['problems']}")
    am = json.load(open(os.path.join(adir, "ARCHIVE_MANIFEST.json"), encoding="utf-8"))
    ws = C.Workspace(root=ws_root).ensure()
    cid = am["candidate_id"]
    ss = json.load(open(os.path.join(adir, "source", "source_set_manifest.json"), encoding="utf-8"))
    _copy(os.path.join(adir, "source", "VEH0135_2026Q2_original.csv"), ws.path(*ss["source"]["path"].split("/")))
    for n in ("manifest.json", "profile.json"):
        _copy(os.path.join(adir, "source", f"registry_version_{n.split('.')[0]}.json"),
              os.path.join(os.path.dirname(ws.path(*ss["source"]["path"].split("/"))), n))
    _copy(os.path.join(adir, "source", "source_set_manifest.json"), ws.path("source_sets", ss["source_set_id"], "manifest.json"))
    rs = json.load(open(os.path.join(adir, "baseline", "release_source_set_manifest.json"), encoding="utf-8"))
    _copy(os.path.join(adir, "baseline", "release_source_set_manifest.json"), ws.path("source_sets", rs["source_set_id"], "manifest.json"))
    _copy(os.path.join(C.PROJECT_ROOT, *rs["source"]["copied_from"].split("/")), ws.path(*rs["source"]["path"].split("/")))
    _copy(os.path.join(adir, "baseline", "reproduction.json"), ws.path("baseline", rs["source_set_id"], "reproduction.json"))
    for rel in json.load(open(os.path.join(adir, "candidate", "manifest.json"), encoding="utf-8"))["files_sha256"]:
        _copy(os.path.join(adir, "candidate", *rel.split("/")), ws.path("candidates", cid, *rel.split("/")))
    _copy(os.path.join(adir, "candidate", "manifest.json"), ws.path("candidates", cid, "manifest.json"))
    _copy(os.path.join(adir, "decision", "decisions.jsonl"), ws.path("candidates", f"{cid}.decisions.jsonl"))
    _copy(os.path.join(adir, "decision", "state.json"), ws.path("candidates", f"{cid}.state.json"))
    pm = json.load(open(os.path.join(adir, "package", "package_manifest.json"), encoding="utf-8"))
    for rel in list(pm["files_sha256"]) + ["package_manifest.json"]:
        _copy(os.path.join(adir, "package", *rel.split("/")), ws.path("release_packages", f"{cid}-B", *rel.split("/")))
    for dp, _, fs in os.walk(ws.path("candidates", cid)):
        for f in fs:
            os.chmod(os.path.join(dp, f), stat.S_IREAD)
    for s_ in (ss, rs):
        S.verified_source_path(ws, S.load(ws, s_["source_set_id"]))
    from .engine import verify_package
    ok, detail = verify_package(ws, cid)
    if not ok:
        raise ValueError(f"restored candidate failed its seal: {detail}")
    return ws


def reproduce(adir, keep=None):
    """Regenerate the candidate from the archived source with the current code; compare every data output."""
    from . import cli
    v = verify_archive(adir)
    if not v["ok"]:
        raise ValueError(f"archive verification failed: {v['problems']}")
    am = json.load(open(os.path.join(adir, "ARCHIVE_MANIFEST.json"), encoding="utf-8"))
    man = json.load(open(os.path.join(adir, "candidate", "manifest.json"), encoding="utf-8"))
    tmp = keep or tempfile.mkdtemp(prefix="c1repro-")
    try:
        ws = C.Workspace(root=tmp).ensure()
        reg = cli.register(ws, os.path.join(adir, "source", "VEH0135_2026Q2_original.csv"))
        base = cli.baseline(ws)
        new = cli.generate(ws, reg["label"])
        d_new = ws.path("candidates", new["candidate_id"])
        same = {f: man["files_sha256"][f] == new["files_sha256"][f] for f in DATA_OUTPUTS}
        a = pd.read_csv(os.path.join(adir, "candidate", "c1_lsoa_latest_vintage.csv")).drop(columns=PROVENANCE_COLS)
        b = pd.read_csv(os.path.join(d_new, "c1_lsoa_latest_vintage.csv")).drop(columns=PROVENANCE_COLS)
        qa_a = pd.read_csv(os.path.join(adir, "candidate", "reports", "qa_checks.csv"))[["check", "passed"]]
        qa_b = pd.read_csv(os.path.join(d_new, "reports", "qa_checks.csv"))[["check", "passed"]]
        report = {"archive": am["seal_sha256"], "source_sha_matches": reg["sha256"] == am["source_sha256"],
                  "october_baseline_reproduced": base["passed"], "data_outputs_identical": same,
                  "lsoa_values_identical_excluding_provenance": a.equals(b), "qa_identical": qa_a.equals(qa_b),
                  "option_b_hashes_identical": new["publication_options"]["B_RESTATED"]["expected_production_sha256"] ==
                  man["publication_options"]["B_RESTATED"]["expected_production_sha256"],
                  "reproduced_qa": new["qa"]}
        report["pass"] = (report["source_sha_matches"] and report["october_baseline_reproduced"] and all(same.values())
                          and report["lsoa_values_identical_excluding_provenance"] and report["qa_identical"]
                          and report["option_b_hashes_identical"])
        return report
    finally:
        if not keep:
            shutil.rmtree(tmp, onerror=lambda f, p, _: (os.chmod(p, stat.S_IWRITE), f(p)))
