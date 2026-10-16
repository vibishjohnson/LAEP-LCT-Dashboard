"""
C1 refresh candidates, end to end in a disposable workspace.

The session fixture registers the real 2026 Q2 VEH0135 through the ingestion pipeline, reproduces the
live release and generates a candidate, all inside a pytest temp directory. Production files are only
read; a module-level guard proves they are unchanged.
"""

import hashlib
import io
import json
import os
import shutil
import stat
import time

import pandas as pd
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
Q2_ARCHIVED = os.path.join(REPO, "experiments", "ev_redistribution_c1", "quarterly_archive", "C1C-2026Q2-ce41d178-20261015T130154",
                           "source", "VEH0135_2026Q2_original.csv")
# The validated 2026 Q2 VEH0135: the committed archive copy, or LCT_VEH0135_2026Q2 pointing at the original download.
Q2 = os.environ.get("LCT_VEH0135_2026Q2") or Q2_ARCHIVED
Q2_SHA = "ce41d17806cf3210a589fcc7bfd9297244e247e48d4a8767f5840879f41f2a6a"
LIVE = os.path.join(REPO, "project", "output_processed", "integration")
WATCH = [os.path.join(LIVE, n) for n in ("ev_actuals_dno.csv", "ev_actuals_la.csv", "ev_actuals_laep.csv", "metadata.json",
                                         "monthly_forecast_dno.csv", "monthly_forecast_la.csv", "monthly_forecast_laep.csv")] + \
        [os.path.join(REPO, "ev", n) for n in os.listdir(os.path.join(REPO, "ev"))] + \
        [os.path.join(REPO, "experiments", "ev_redistribution_c1", "outputs", "c1_lsoa_quarterly.csv"),
         os.path.join(REPO, "experiments", "ev_redistribution_c1", "release", "state", "releases", "c1-20261014-ukpn-ev", "release_manifest.json"),
         os.path.join(REPO, "project", "output_processed", "lsoa_ev_bodytype.csv")]

sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()


@pytest.fixture(scope="module", autouse=True)
def production_untouched():
    before = {p: (sha(p), os.path.getmtime(p)) for p in WATCH if os.path.isfile(p)}
    yield
    assert {p: (sha(p), os.path.getmtime(p)) for p in WATCH if os.path.isfile(p)} == before


@pytest.fixture(scope="module")
def ws(tmp_path_factory):
    if not os.path.exists(Q2) or sha(Q2) != Q2_SHA:
        pytest.skip("the validated 2026 Q2 VEH0135 download is not present on this machine")
    root = str(tmp_path_factory.mktemp("c1ws"))
    os.environ["LCT_C1_WORKSPACE"] = root
    from data_manager.c1_refresh import cli, config as C
    w = C.Workspace(root=root).ensure()
    reg = cli.register(w, Q2)
    base = cli.baseline(w)
    man = cli.generate(w, reg["label"])
    return {"ws": w, "reg": reg, "baseline": base, "man": man, "cid": man["candidate_id"]}


def cand(ws, *p):
    return ws["ws"].path("candidates", ws["cid"], *p)


# ------------------------------------------------------------------ source sets
def test_registered_source_is_the_validated_bytes(ws):
    from data_manager.c1_refresh import sources
    assert ws["reg"]["sha256"] == Q2_SHA and ws["reg"]["period_label"] == "2026 Q2" and ws["reg"]["rows"] == 286113
    sm = sources.load(ws["ws"], ws["man"]["source_set_id"])
    assert sm["source"]["sha256"] == Q2_SHA and sm["kind"] == "REGISTERED_VERSION"
    assert sm["quarters"] == ["2025 Q1", "2025 Q2", "2025 Q3", "2025 Q4", "2026 Q1", "2026 Q2"]
    assert sm["periods"][0] == "2025-03" and sm["live_release"]["release_id"] == "c1-20261014-ukpn-ev"
    assert set(sm["lookups"]) == {"lsoa_dno", "geography_lookup"} and sm["methodology"]["lpn_min_share"] == 0.20
    assert sm["historical_vintages"][0]["source_veh0135_sha256_prefix"] == "ba54465c0ebfb7bb"
    assert sources.verified_source_path(ws["ws"], sm).endswith("original.csv")
    d = ws["ws"].path("source_sets", sm["source_set_id"])
    assert all(not os.stat(os.path.join(d, n)).st_mode & stat.S_IWRITE for n in os.listdir(d))


def test_registering_the_same_bytes_reuses_the_version(ws):
    from data_manager.c1_refresh import cli
    again = cli.register(ws["ws"], Q2)
    assert again["reused"] and again["label"] == ws["reg"]["label"]


def test_no_fallback_to_ev_or_unknown_sources(ws, tmp_path):
    from data_manager.c1_refresh import sources
    with pytest.raises(ValueError, match="no registered VEH0135 version"):
        sources.from_registered_version(ws["ws"], "VEH0135-2026Q3-v1")
    with pytest.raises(ValueError, match="not the source the live release"):
        sources.from_release_source(ws["ws"], Q2)
    # tampered source bytes are detected before use
    sm = sources.load(ws["ws"], ws["man"]["source_set_id"])
    copy = tmp_path / "ws2"
    shutil.copytree(ws["ws"].root, copy)
    from data_manager.c1_refresh.config import Workspace
    w2 = Workspace(root=str(copy))
    p = w2.path(*sm["source"]["path"].split("/"))
    os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
    with open(p, "ab") as f:
        f.write(b"\n")
    with pytest.raises(ValueError, match="changed"):
        sources.verified_source_path(w2, sm)


# ------------------------------------------------------------------ baseline
def test_baseline_reproduces_live_release_exactly(ws):
    b = ws["baseline"]
    assert b["passed"] and b["numeric_identical"] and b["rows_published"] == b["rows_reproduced"] == 110230
    assert all(v == 0 for v in b["numeric_cell_differences"].values())
    assert all(b["integration_bytes_identical"].values()) and b["method_a_rows"] == 0
    assert b["totals"]["2026-03"] == {"UKPN": 896098, "EPN": 435157, "LPN": 191386, "SPN": 269555, "uplift": 0}
    assert set(b["provenance_fields_differ"]) == {"source_veh0135_sha256", "source_lsoa_dno_sha256", "code_git_head", "run_utc"}


def test_candidate_refused_without_passing_baseline(ws, tmp_path):
    from data_manager.c1_refresh import cli, engine
    from data_manager.c1_refresh.config import Workspace
    copy = tmp_path / "ws3"
    shutil.copytree(ws["ws"].root, copy)
    w3 = Workspace(root=str(copy))
    rid = cli.release_set_id(w3)
    p = w3.path("baseline", rid, "reproduction.json")
    r = json.load(open(p))
    r["passed"] = False
    json.dump(r, open(p, "w"))
    with pytest.raises(ValueError, match="baseline reproduction has not passed"):
        engine.build_candidate(w3, ws["man"]["source_set_id"], rid)
    with pytest.raises(ValueError, match="only from registered"):
        engine.build_candidate(w3, rid, rid)


# ------------------------------------------------------------------ candidate content
def test_candidate_qa_all_passed_with_evidence(ws):
    qa = pd.read_csv(cand(ws, "reports", "qa_checks.csv"))
    assert qa.passed.all() and len(qa) == ws["man"]["qa"]["total"] >= 250
    for needle in ("national BEV conserved", "national PHEV conserved", "Miscellaneous receives nothing", "keeper components sum to raw",
                   "same-quarter private stock share", "largest-remainder rounding deterministic", "LPN floor 0.20",
                   "DNO = LA = sum of LSOAs", "each LAEP = sum of its mapped LAs", "only C1 observations", "unchanged during generation"):
        assert qa.check.str.contains(needle, regex=False).any(), needle
    assert set(qa[qa.check.str.contains("national BEV conserved", regex=False)].period) == {"2025-03", "2025-06", "2025-09", "2025-12", "2026-03", "2026-06"}


def test_integration_structure_six_quarters(ws):
    for opt in ("append_only", "restated"):
        rows = {n: len(pd.read_csv(cand(ws, "integration", opt, n))) for n in ("ev_actuals_dno.csv", "ev_actuals_la.csv", "ev_actuals_laep.csv")}
        assert rows == {"ev_actuals_dno.csv": 36, "ev_actuals_la.csv": 1452, "ev_actuals_laep.csv": 168}, opt
        la = pd.read_csv(cand(ws, "integration", opt, "ev_actuals_la.csv"))
        laep = pd.read_csv(cand(ws, "integration", opt, "ev_actuals_laep.csv"))
        assert la.LAD22CD.nunique() == 121 and laep.LAEP.nunique() == 14
        assert sorted(la.period.unique()) == ["2025-03", "2025-06", "2025-09", "2025-12", "2026-03", "2026-06"]
    lsoa = pd.read_csv(cand(ws, "c1_lsoa_latest_vintage.csv"), usecols=["period", "LSOA21CD", "methodology_id", "lpn_min_share"])
    assert lsoa.groupby("period").LSOA21CD.nunique().eq(11023).all() and set(lsoa.methodology_id) == {"C1_DYNAMIC_PRIVATE_EV"}
    assert set(lsoa.lpn_min_share) == {0.2}


def test_append_only_preserves_published_history_exactly(ws):
    for n in ("ev_actuals_dno.csv", "ev_actuals_la.csv", "ev_actuals_laep.csv"):
        live = open(os.path.join(LIVE, n), "rb").read()
        app = open(cand(ws, "integration", "append_only", n), "rb").read()
        res = open(cand(ws, "integration", "restated", n), "rb").read()
        assert app.startswith(live) and len(app) > len(live)
        new_app = pd.read_csv(io.BytesIO(app)).query("period == '2026-06'").reset_index(drop=True)
        new_res = pd.read_csv(io.BytesIO(res)).query("period == '2026-06'").reset_index(drop=True)
        assert new_app.equals(new_res)                          # the new quarter is the same in both options
        assert app != res                                       # historical revisions exist and are isolated in option B
    pub_path = os.path.join(REPO, "experiments", "ev_redistribution_c1", "outputs", "c1_lsoa_quarterly.csv")
    app_bytes = open(cand(ws, "c1_lsoa_append_only.csv"), "rb").read()
    assert app_bytes.startswith(open(pub_path, "rb").read())             # published LSOA history kept byte for byte
    app_lsoa = pd.read_csv(io.BytesIO(app_bytes))
    assert app_lsoa[app_lsoa.period != "2026-06"].reset_index(drop=True).equals(pd.read_csv(pub_path))
    assert (app_lsoa.period == "2026-06").sum() == 2 * 11023


def test_restatement_report_is_consistent(ws):
    geo = pd.read_csv(cand(ws, "reports", "restatement_geography.csv"))
    assert set(geo.period) == {"2025-03", "2025-06", "2025-09", "2025-12", "2026-03"}            # never the new quarter
    uk = geo[(geo.level == "UKPN") & (geo.EV_Type == "Total")].set_index("period")
    dno = geo[(geo.level == "DNO") & (geo.EV_Type == "Total")].groupby("period")[["published", "recalculated"]].sum()
    la = geo[(geo.level == "LA") & (geo.EV_Type == "Total")].groupby("period")[["published", "recalculated"]].sum()
    assert (uk[["published", "recalculated"]] == dno).all().all() and (dno == la).all().all()
    live = pd.read_csv(os.path.join(LIVE, "ev_actuals_dno.csv")).groupby("period").install_count.sum()
    assert (uk.published == live.reindex(uk.index)).all()                                           # "published" really is live
    assert ((uk.recalculated - uk.published) == uk["diff"]).all()
    lsoa = pd.read_csv(cand(ws, "reports", "restatement_lsoa_changes.csv"))
    assert (lsoa["diff"] != 0).all() or lsoa.empty
    s = json.load(open(cand(ws, "reports", "summary.json")))
    assert s["new_quarter_values"]["UKPN"] == s["new_quarter_values"]["EPN"] + s["new_quarter_values"]["LPN"] + s["new_quarter_values"]["SPN"]
    assert s["new_quarter_values"]["UKPN"] == s["new_quarter_values"]["BEV"] + s["new_quarter_values"]["PHEV"]
    floor = pd.DataFrame(s["lpn_floor"])
    assert set(floor.vintage.str[:6]) == {"publis", "latest"} and (floor.lpn_uplift >= 0).all()


def test_package_sealed_and_tamper_evident(ws, tmp_path):
    from data_manager.c1_refresh import engine
    from data_manager.c1_refresh.config import Workspace
    ok, detail = engine.verify_package(ws["ws"], ws["cid"])
    assert ok, detail
    copy = tmp_path / "ws4"
    shutil.copytree(ws["ws"].root, copy)
    w4 = Workspace(root=str(copy))
    p = w4.path("candidates", ws["cid"], "integration", "append_only", "ev_actuals_dno.csv")
    os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
    open(p, "ab").write(b"x")
    ok, detail = engine.verify_package(w4, ws["cid"])
    assert not ok and detail["changed_files"] == ["integration/append_only/ev_actuals_dno.csv"]


def test_partial_candidates_are_never_listed(ws):
    from data_manager.c1_refresh import store
    os.makedirs(ws["ws"].path("candidates", "C1C-bogus.partial"), exist_ok=True)
    ids = [c["manifest"]["candidate_id"] for c in store.list_candidates(ws["ws"])]
    assert "C1C-bogus.partial" not in ids and ws["cid"] in ids


# ------------------------------------------------------------------ decisions
def _copy_candidate(ws, tmp_path, name):
    from data_manager.c1_refresh.config import Workspace
    copy = tmp_path / name
    shutil.copytree(ws["ws"].root, copy)
    return Workspace(root=str(copy))


def test_state_model_and_decision_rules(ws, tmp_path):
    from data_manager.c1_refresh import store
    w = _copy_candidate(ws, tmp_path, "wsA")
    cid = ws["cid"]
    st = store.state(w, cid)
    assert [h["state"] for h in st["history"]] == ["SOURCE_VALIDATED", "CANDIDATE_GENERATED", "QA_PASSED", "RESTATEMENTS_DETECTED",
                                                   "AWAITING_RESTATEMENT_DECISION"]
    with pytest.raises(store.DecisionRefused, match="named decider"):
        store.decide(w, cid, "A", "", "", f"APPEND_ONLY {cid}")
    with pytest.raises(store.DecisionRefused, match="confirmation must be exactly"):
        store.decide(w, cid, "A", "Analyst", "TICKET-1", "yes")
    with pytest.raises(store.DecisionRefused, match="restatement approval"):
        store.decide(w, cid, "B", "Analyst", "TICKET-1", f"RESTATED {cid}")
    man = json.load(open(w.path("candidates", cid, "manifest.json")))
    with pytest.raises(store.DecisionRefused, match="restatement approval"):
        store.decide(w, cid, "B", "Analyst", "TICKET-1", f"RESTATED {cid}", {"approved": True, "reason": "x", "report_sha256": "0" * 64})
    rec = store.decide(w, cid, "A", "Analyst", "TICKET-1", f"APPEND_ONLY {cid}")
    assert rec["not_a_release_authorisation"] and rec["expected_production_sha256"] == man["publication_options"]["A_APPEND_ONLY"]["expected_production_sha256"]
    assert store.state(w, cid)["state"] == "APPROVED_FOR_FUTURE_RELEASE" and store.verify_chain(w, cid)
    with pytest.raises(store.DecisionRefused, match="already"):
        store.decide(w, cid, "C", "Analyst", "TICKET-2", f"REJECT_OR_DEFER {cid}")
    # the real candidate is untouched by decisions made on the copy
    assert store.state(ws["ws"], cid)["state"] == "AWAITING_RESTATEMENT_DECISION" and store.decisions(ws["ws"], cid) == []


def test_option_b_needs_bound_restatement_approval_and_c_rejects(ws, tmp_path):
    from data_manager.c1_refresh import store
    cid = ws["cid"]
    w = _copy_candidate(ws, tmp_path, "wsB")
    rep = json.load(open(w.path("candidates", cid, "manifest.json")))["files_sha256"]["reports/restatement_geography.csv"]
    rec = store.decide(w, cid, "B", "Analyst", "TICKET-3", f"RESTATED {cid}", {"approved": True, "reason": "DfT revision accepted", "report_sha256": rep})
    assert rec["restatement_approval"]["report_sha256"] == rep and store.state(w, cid)["state"] == "APPROVED_FOR_FUTURE_RELEASE"
    w = _copy_candidate(ws, tmp_path, "wsC")
    store.decide(w, cid, "C", "Analyst", "TICKET-4", f"REJECT_OR_DEFER {cid}")
    assert store.state(w, cid)["state"] == "REJECTED"


def test_decision_log_tampering_detected_and_tampered_package_refused(ws, tmp_path):
    from data_manager.c1_refresh import store
    cid = ws["cid"]
    w = _copy_candidate(ws, tmp_path, "wsD")
    store.decide(w, cid, "A", "Analyst", "TICKET-5", f"APPEND_ONLY {cid}")
    _, dp = store._paths(w, cid)
    recs = open(dp).read().replace("TICKET-5", "TICKET-9")
    open(dp, "w").write(recs)
    assert not store.verify_chain(w, cid)
    w = _copy_candidate(ws, tmp_path, "wsE")
    p = w.path("candidates", cid, "reports", "qa_checks.csv")
    os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
    open(p, "a").write("tampered\n")
    with pytest.raises(store.DecisionRefused, match="failed verification"):
        store.decide(w, cid, "A", "Analyst", "TICKET-6", f"APPEND_ONLY {cid}")


def test_qa_failed_candidate_can_only_be_rejected(ws, tmp_path):
    from data_manager.c1_refresh import store
    cid = ws["cid"]
    w = _copy_candidate(ws, tmp_path, "wsF")
    sp, _ = store._paths(w, cid)
    st = json.load(open(sp))
    st.update(qa_failed=True, state="CANDIDATE_GENERATED")
    json.dump(st, open(sp, "w"))
    with pytest.raises(store.DecisionRefused, match="QA-passed"):
        store.decide(w, cid, "A", "Analyst", "T", f"APPEND_ONLY {cid}")
    store.decide(w, cid, "C", "Analyst", "T", f"REJECT_OR_DEFER {cid}")


# ------------------------------------------------------------------ web
def test_web_generate_review_and_decide(ws, tmp_path):
    from flask import Flask
    from data_manager.c1_refresh import web
    w = _copy_candidate(ws, tmp_path, "wsW")
    app = Flask("t", template_folder=os.path.join(REPO, "templates"))
    web.init_app(app, w, memory_mb=1500, timeout_s=1200)
    c = app.test_client()
    assert c.post("/api/c1-refresh/generate", data="x").status_code == 415
    assert c.post("/api/c1-refresh/generate", json={"version_label": "nope"}).status_code == 400
    r = c.post("/api/c1-refresh/generate", json={"version_label": ws["reg"]["label"]})
    assert r.status_code == 202
    jid = r.get_json()["job"]
    assert c.post("/api/c1-refresh/generate", json={"version_label": ws["reg"]["label"]}).status_code == 429   # one at a time
    for _ in range(900):
        j = c.get(f"/api/c1-refresh/jobs/{jid}").get_json()["job"]
        if j["state"] in ("COMPLETE", "FAILED") and "worker_peak_commit_mb" in j:
            break
        time.sleep(1)
    assert j["state"] == "COMPLETE", j
    assert j["worker_peak_commit_mb"] < 1500
    new = j["candidate_id"]
    page = c.get(f"/c1-refresh/{new}").get_data(as_text=True)
    assert "Sealed package verified" in page and "AWAITING_RESTATEMENT_DECISION" in page and "953,624" in page
    assert c.get("/c1-refresh").status_code == 200 and c.get(f"/c1-refresh/{new}/download/qa").status_code == 200
    assert c.get(f"/c1-refresh/{new}/download/../../x").status_code == 404
    bad = c.post(f"/api/c1-refresh/{new}/decision", json={"option": "A", "decided_by": "A", "evidence": "E", "confirmation": "no"})
    assert bad.status_code == 409
    ok = c.post(f"/api/c1-refresh/{new}/decision", json={"option": "C", "decided_by": "A", "evidence": "E", "confirmation": f"REJECT_OR_DEFER {new}"})
    assert ok.status_code == 200 and "REJECTED" in c.get(f"/c1-refresh/{new}").get_data(as_text=True)
