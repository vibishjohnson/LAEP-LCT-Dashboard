"""
Candidate state and decisions (outside the sealed package, append-only).

States: SOURCE_VALIDATED -> CANDIDATE_GENERATED -> QA_PASSED -> RESTATEMENTS_DETECTED ->
AWAITING_RESTATEMENT_DECISION -> APPROVED_FOR_FUTURE_RELEASE | REJECTED.
QA passing never approves anything. A decision needs a named decider, evidence and a typed
confirmation; option B additionally needs an explicit restatement approval bound to the hash of the
revision report. Each record carries the hash of the previous one (tamper-evident chain).
A recorded decision is not authorisation to run a production release.
"""

import json
import os

from . import config as C
from . import sources as S

STATES = ("SOURCE_VALIDATED", "CANDIDATE_GENERATED", "QA_PASSED", "RESTATEMENTS_DETECTED",
          "AWAITING_RESTATEMENT_DECISION", "APPROVED_FOR_FUTURE_RELEASE", "REJECTED")
OPTIONS = {"A": "APPEND_ONLY", "B": "RESTATED", "C": "REJECT_OR_DEFER"}


class DecisionRefused(ValueError):
    pass


def _paths(ws, cid):
    return ws.path("candidates", cid, "..", f"{cid}.state.json"), ws.path("candidates", cid, "..", f"{cid}.decisions.jsonl")


def init_state(ws, cid, manifest):
    """Walk the generated candidate through the automatic states (recorded with evidence)."""
    hist = [{"state": "SOURCE_VALIDATED", "at": manifest["created_at"], "evidence": manifest["source_set_id"]},
            {"state": "CANDIDATE_GENERATED", "at": manifest["created_at"], "evidence": manifest["seal_sha256"]}]
    if manifest["qa"]["all_passed"]:
        hist.append({"state": "QA_PASSED", "at": S.now(), "evidence": f"{manifest['qa']['passed']}/{manifest['qa']['total']} checks"})
        if manifest["restatements_detected"]:
            hist.append({"state": "RESTATEMENTS_DETECTED", "at": S.now(),
                         "evidence": "append-only and restated integration files differ (see reports/restatement_*.csv)"})
        hist.append({"state": "AWAITING_RESTATEMENT_DECISION", "at": S.now(), "evidence": "user decision required: A, B or C"})
    state = {"candidate_id": cid, "state": hist[-1]["state"], "history": hist, "qa_failed": not manifest["qa"]["all_passed"]}
    sp, _ = _paths(ws, cid)
    with open(sp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    return state


def state(ws, cid):
    sp, _ = _paths(ws, cid)
    with open(sp, encoding="utf-8") as f:
        return json.load(f)


def decisions(ws, cid):
    _, dp = _paths(ws, cid)
    if not os.path.exists(dp):
        return []
    with open(dp, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def verify_chain(ws, cid):
    prev = None
    for d in decisions(ws, cid):
        if d["previous_record_sha256"] != prev or d["record_sha256"] != S.seal({k: v for k, v in d.items() if k != "record_sha256"} | {"seal_sha256": None}):
            return False
        prev = d["record_sha256"]
    return True


def decide(ws, cid, option, decided_by, evidence, confirmation, restatement_approval=None):
    from .engine import verify_package
    st = state(ws, cid)
    if option not in OPTIONS:
        raise DecisionRefused("option must be A, B or C")
    if st["state"] in ("APPROVED_FOR_FUTURE_RELEASE", "REJECTED"):
        raise DecisionRefused(f"candidate is already {st['state']}")
    ok, detail = verify_package(ws, cid)
    if not ok:
        raise DecisionRefused(f"sealed package failed verification: {detail}")
    if not (decided_by or "").strip() or not (evidence or "").strip():
        raise DecisionRefused("a named decider and approval evidence (e.g. ticket or email reference) are required")
    if confirmation != f"{OPTIONS[option]} {cid}":
        raise DecisionRefused(f"confirmation must be exactly: {OPTIONS[option]} {cid}")
    man = json.load(open(ws.path("candidates", cid, "manifest.json"), encoding="utf-8"))
    if option in ("A", "B") and (st["qa_failed"] or st["state"] != "AWAITING_RESTATEMENT_DECISION"):
        raise DecisionRefused("only a QA-passed candidate awaiting a decision can be approved for a future release")
    report_sha = man["files_sha256"]["reports/restatement_geography.csv"]
    if option == "B":
        ra = restatement_approval or {}
        if not ra.get("approved") or not (ra.get("reason") or "").strip() or ra.get("report_sha256") != report_sha:
            raise DecisionRefused("option B needs an explicit restatement approval with a reason, bound to the revision report "
                                  f"(sha256 {report_sha})")
    prev = decisions(ws, cid)
    rec = {"candidate_id": cid, "source_set_id": man["source_set_id"], "candidate_seal_sha256": man["seal_sha256"],
           "option": option, "option_name": OPTIONS[option], "decided_at": S.now(), "decided_by": decided_by.strip(),
           "approval_evidence": evidence.strip(), "restatement_approval": restatement_approval if option == "B" else None,
           "restatement_report_sha256": report_sha,
           "expected_production_sha256": man["publication_options"].get({"A": "A_APPEND_ONLY", "B": "B_RESTATED"}.get(option, ""), {}).get("expected_production_sha256"),
           "not_a_release_authorisation": True, "previous_record_sha256": prev[-1]["record_sha256"] if prev else None}
    rec["record_sha256"] = S.seal(rec | {"seal_sha256": None})
    _, dp = _paths(ws, cid)
    with open(dp, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
    new_state = "REJECTED" if option == "C" else "APPROVED_FOR_FUTURE_RELEASE"
    st["history"].append({"state": new_state, "at": rec["decided_at"], "evidence": rec["record_sha256"]})
    st["state"] = new_state
    sp, _ = _paths(ws, cid)
    with open(sp + ".tmp", "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2)
    os.replace(sp + ".tmp", sp)
    return rec


def list_candidates(ws):
    d = ws.path("candidates")
    out = []
    for n in sorted(os.listdir(d), reverse=True) if os.path.isdir(d) else []:
        if n.startswith("C1C-") and os.path.isdir(os.path.join(d, n)) and not n.endswith(".partial"):
            try:
                man = json.load(open(os.path.join(d, n, "manifest.json"), encoding="utf-8"))
                out.append({"manifest": man, "state": state(ws, n)})
            except (OSError, ValueError):
                out.append({"manifest": {"candidate_id": n}, "state": {"state": "UNREADABLE"}})
    return out
