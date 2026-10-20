"""
SMMT provisional nowcast: the C1-anchored candidate generator (fixed licence-area shares), the data-manager workflow
and the dashboard series (geography of the body-type file: tests/test_ev_bodytype_geography.py).

Candidates are generated into a temporary folder; live integration files and dashboard.html are only read (and are
checked unchanged). Run from the repository root: python -m pytest tests/test_smmt_nowcast.py -q -p no:cacheprovider
"""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from data_manager import smmt_dashboard_patch as patch  # noqa: E402
from data_manager.smmt_candidate_builder import (FORECAST_SOURCE, INTEGRATION_REL, LIVE_WATCH,  # noqa: E402
                                                 SMmtCandidateBuilder, largest_remainder)

OUT = os.path.join(REPO, "project", "output_processed")
INTEG = os.path.join(REPO, *INTEGRATION_REL.split("/"))
HARNESS = os.path.join(REPO, "experiments", "ev_redistribution_c1", "dashboard_regression", "dashboard_harness.js")
sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
WATCH = [os.path.join(INTEG, n) for n in LIVE_WATCH] + [os.path.join(REPO, "dashboard.html"), os.path.join(OUT, "lsoa_ev_bodytype.csv")]


@pytest.fixture(scope="module", autouse=True)
def live_untouched():
    before = {p: (sha(p), os.path.getmtime(p)) for p in WATCH}
    yield
    assert {p: (sha(p), os.path.getmtime(p)) for p in WATCH} == before


# ---------------------------------------------------------------- candidate generator
@pytest.fixture(scope="module")
def builder(tmp_path_factory):
    b = SMmtCandidateBuilder(REPO)
    b.candidates_dir = str(tmp_path_factory.mktemp("smmt_candidates"))
    return b


@pytest.fixture(scope="module")
def cand(builder):
    m = builder.generate()
    assert m["status"] == "VALIDATED", m.get("errors")
    return m


def _files(builder, m):
    f = builder.folder(m["candidate_id"])
    return {n: pd.read_csv(os.path.join(f, f"smmt_nowcast_{n}.csv"), dtype={"LAD22CD": str}) for n in ("dno", "la", "laep")}, \
        pd.read_csv(os.path.join(f, "nowcast_lsoa.csv"), dtype={"LAD22CD": str}), pd.read_csv(os.path.join(f, "ukpn_monthly_summary.csv"))


def test_candidate_qa_all_pass_and_sealed(builder, cand):
    assert cand["qa_passed"] == cand["qa_total"] >= 18
    assert builder.verify(cand["candidate_id"])[0]
    assert builder.status(cand["candidate_id"])["state"] == "VALIDATED"
    assert cand["published"] is False


def test_candidate_months_are_exactly_the_supplied_contiguous_smmt_months(builder, cand):
    meta = json.load(open(os.path.join(INTEG, "metadata.json"), encoding="utf-8"))
    entries = builder.smmt_entries()
    have = {f"{e['year']}-{e['month']:02d}" for e in entries}
    p, expect = pd.Period(meta["latest_actual_period"], freq="M") + 1, []
    while str(p) in have:
        expect.append(str(p))
        p += 1
    assert cand["baseline_period"] == meta["latest_actual_period"]
    assert cand["nowcast_months"] == expect and cand["latest_nowcast_month"] == expect[-1]
    geo, lsoa, _ = _files(builder, cand)
    for g in geo.values():
        assert sorted(g["period"].unique()) == expect          # nothing at the baseline, nothing beyond the data
        assert (g["forecast_source"] == FORECAST_SOURCE).all()


def test_candidate_baseline_is_the_live_c1_actual_and_additions_follow_smmt(builder, cand):
    geo, lsoa, s = _files(builder, cand)
    live = pd.read_csv(os.path.join(INTEG, "ev_actuals_dno.csv"))
    base = live[live["period"] == cand["baseline_period"]].groupby("EV_Type")["install_count"].sum()
    assert {k: int(v) for k, v in base.items()} == cand["ukpn_baseline_stock"]
    dm = pd.read_csv(os.path.join(builder.folder(cand["candidate_id"]), "dno_monthly_summary.csv"))
    live_b = live[live["period"] == cand["baseline_period"]]
    for f in ("BEV", "PHEV"):
        uk = cand["uk_stock_at_baseline"][f]
        shares = {d: int(live_b[(live_b.DNO == d) & (live_b.EV_Type == f)].install_count.sum()) / uk for d in ("EPN", "LPN", "SPN")}
        for d, s_d in shares.items():                          # fixed shares of total UK stock, per fuel and licence area
            assert abs(cand["dno_share_of_uk_stock"][f][d] - s_d) < 1e-12
        cum = {d: 0 for d in shares}
        for m in cand["nowcast_months"]:
            regs = cand["smmt_uk_registrations"][m][f]
            split = largest_remainder(np.array(list(shares.values()) + [1 - sum(shares.values())]), regs)
            for i, d in enumerate(shares):
                cum[d] += int(split[i])
                row = dm[(dm.period == m) & (dm.EV_Type == f) & (dm.DNO == d)].iloc[0]
                assert row["additions"] == split[i] and row["cumulative_additions"] == cum[d]
                base = int(live_b[(live_b.DNO == d) & (live_b.EV_Type == f)].install_count.sum())
                assert row["nowcast_stock"] == base + cum[d]
                assert abs(row["additions"] - regs * shares[d]) < 1    # largest-remainder rounding only
            u = s[(s["period"] == m) & (s["EV_Type"] == f)].iloc[0]
            assert u["ukpn_cumulative_additions"] == sum(cum.values())
            assert u["ukpn_nowcast_stock"] == cand["ukpn_baseline_stock"][f] + sum(cum.values())


def test_candidate_reconciles_lsoa_dno_la_laep(builder, cand):
    geo, lsoa, s = _files(builder, cand)
    assert not lsoa.duplicated(["period", "LSOA21CD", "EV_Type"]).any()
    ukpn = s.set_index(["period", "EV_Type"])["ukpn_nowcast_stock"]
    assert (lsoa.groupby(["period", "EV_Type"])["nowcast_stock"].sum() == ukpn).all()
    assert (geo["dno"].groupby(["period", "EV_Type"])["value"].sum() == ukpn).all()
    assert (geo["la"].groupby(["period", "EV_Type"])["value"].sum() == ukpn).all()
    laep_lsoa = lsoa[lsoa["LAEP"].notna()].groupby(["period", "EV_Type"])["nowcast_stock"].sum()
    assert (geo["laep"].groupby(["period", "EV_Type"])["value"].sum() == laep_lsoa).all()
    # the same LAEP coverage as the live actuals at the baseline
    live_laep = pd.read_csv(os.path.join(INTEG, "ev_actuals_laep.csv"))
    b = lsoa[lsoa["period"] == cand["nowcast_months"][0]]
    assert set(live_laep["LAEP"]) == set(b["LAEP"].dropna())
    assert (lsoa.sort_values("period").groupby(["LSOA21CD", "EV_Type"])["cumulative_addition"].diff().dropna() >= 0).all()


def test_candidate_is_deterministic(builder, cand):
    again = builder.generate()
    assert again["data_sha256"] == cand["data_sha256"]
    assert again["diff_vs_previous"]["same_data"] is True


def test_largest_remainder_conserves_and_breaks_ties_by_position():
    w = np.array([1.0, 1.0, 1.0])
    assert largest_remainder(w, 2).tolist() == [1, 1, 0]
    rng = np.random.default_rng(0)
    for _ in range(50):
        w = rng.random(200)
        t = int(rng.integers(0, 5000))
        r = largest_remainder(w, t)
        assert r.sum() == t and (r >= 0).all() and (np.abs(r - w / w.sum() * t) < 1).all()


def test_stale_when_smmt_figures_change(builder, cand, monkeypatch):
    real = builder.smmt_entries()
    changed = [dict(e, bev_cars=e["bev_cars"] + 1) if i == 0 else e for i, e in enumerate(real)]
    monkeypatch.setattr(builder, "smmt_entries", lambda: changed)
    assert builder.status(cand["candidate_id"])["state"] == "STALE"


def test_tampered_candidate_detected(builder, tmp_path):
    m = builder.generate()
    p = os.path.join(builder.folder(m["candidate_id"]), "smmt_nowcast_dno.csv")
    with open(p, "a", encoding="utf-8") as f:
        f.write("EPN,1,month,EV,BEV,SMMT_NOWCAST,x,,,stock,SMMT,2026-09,2026-09-30,False,False,,\n")
    assert builder.status(m["candidate_id"])["state"] == "TAMPERED"


def test_rejected_state_requires_reason(builder):
    m = builder.generate()
    with pytest.raises(Exception):
        builder.reject(m["candidate_id"], "  ")
    builder.reject(m["candidate_id"], "test rejection")
    assert builder.status(m["candidate_id"])["state"] == "REJECTED"


def _db_with(tmp_path, rows):
    p = str(tmp_path / "db.sqlite")
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE smmt_entries (id INTEGER, year INTEGER, month INTEGER, bev_cars INTEGER, bev_vans INTEGER, "
                "phev_cars INTEGER, phev_vans INTEGER, entered_at TEXT, notes TEXT, is_current INTEGER)")
    con.executemany("INSERT INTO smmt_entries VALUES (?,?,?,?,?,?,?,?,?,1)", rows)
    con.commit()
    con.close()
    return p


def test_failed_candidate_when_no_smmt_after_baseline(tmp_path):
    b = SMmtCandidateBuilder(REPO, db_path=_db_with(tmp_path, [(1, 2026, 2, 10, 1, 5, 0, "x", "")]))
    b.candidates_dir = str(tmp_path / "c")
    m = b.generate()
    assert m["status"] == "FAILED" and "first month after the latest C1 actual" in m["errors"][0]
    assert sorted(os.listdir(b.folder(m["candidate_id"]))) == ["manifest.json"]   # no data written
    assert b.status(m["candidate_id"])["state"] == "FAILED"


def test_gap_in_smmt_months_stops_the_nowcast(tmp_path):
    rows = [(1, 2026, 4, 100, 10, 50, 0, "x", ""), (2, 2026, 5, 100, 10, 50, 0, "x", ""), (3, 2026, 7, 100, 10, 50, 0, "x", "")]
    b = SMmtCandidateBuilder(REPO, db_path=_db_with(tmp_path, rows))
    b.candidates_dir = str(tmp_path / "c")
    m = b.generate()
    assert m["status"] == "VALIDATED" and m["nowcast_months"] == ["2026-04", "2026-05"]
    assert m["month_info"]["ignored_after_gap"] == ["2026-07"] and any("gap" in w for w in m["warnings"])


def test_negative_smmt_values_fail(tmp_path):
    b = SMmtCandidateBuilder(REPO, db_path=_db_with(tmp_path, [(1, 2026, 4, -5, 10, 50, 0, "x", "")]))
    b.candidates_dir = str(tmp_path / "c")
    assert b.generate()["status"] == "FAILED"


def test_june_c1_publication_rebaselines_the_nowcast():
    """Disposable environment: publishing the sealed June Option B package makes March-based candidates STALE, and a
    regenerated candidate starts from the published June actuals using that release's own LSOA output and VEH0135."""
    from data_manager.c1_refresh import config as C, release_readiness as rr
    import c1_quarterly_release as cq
    import c1_release as rel
    from data_manager.db import DB_PATH
    arch = "experiments/ev_redistribution_c1/quarterly_archive/C1C-2026Q2-ce41d178-20261015T130154"
    if not os.path.isdir(os.path.join(REPO, *arch.split("/"))):
        pytest.skip("sealed June C1 archive not present")
    env = os.path.join(C.short_tmp(), f"smmtjune-{os.getpid()}")
    work = env + "-auth"
    os.makedirs(work, exist_ok=True)
    try:
        rr.build_chain_env(env)
        os.makedirs(os.path.join(env, "ev"), exist_ok=True)
        shutil.copyfile(os.path.join(REPO, "ev", "df_VEH0135.csv"), os.path.join(env, "ev", "df_VEH0135.csv"))
        shutil.copytree(os.path.join(REPO, *arch.split("/")), os.path.join(env, *arch.split("/")))
        b = SMmtCandidateBuilder(env, db_path=DB_PATH)
        b.candidates_dir = os.path.join(env, "project", "dft_staging", "smmt_candidates")
        march = b.generate()
        assert march["status"] == "VALIDATED" and march["baseline_period"] == "2026-03"
        pkg = os.path.join(env, *arch.split("/"), "package")
        pm = cq.load_package(pkg)
        rid = "c1-20261019-smmt-rebaseline-test"          # release ids must be c1-YYYYMMDD-...
        auth = rr.write_authorisation(os.path.join(work, "auth.json"), pm, rid)
        pf = cq.preflight(env, pkg, auth, rid)
        assert pf["ok"]
        cq.promote(env, pkg, auth, rid, rel.confirm_token("PROMOTE", rid, pf["digest"]))
        assert b.status(march["candidate_id"])["state"] == "STALE"
        june = b.generate()
        assert june["status"] == "VALIDATED", june.get("errors")
        assert june["baseline_period"] == "2026-06" and june["nowcast_months"][0] == "2026-07"
        assert june["inputs"]["c1_release"] == rid and "/candidate/c1_lsoa_latest_vintage.csv" in june["inputs"]["c1_lsoa_path"]
        live = pd.read_csv(os.path.join(env, *INTEGRATION_REL.split("/"), "ev_actuals_dno.csv"))
        assert {f: int(live[(live.period == "2026-06") & (live.EV_Type == f)].install_count.sum()) for f in ("BEV", "PHEV")} \
            == june["ukpn_baseline_stock"]
    finally:
        rr.remove_env(env)
        rr.remove_env(work)


# ---------------------------------------------------------------- data manager
def test_no_hard_coded_candidate_values_in_ui():
    src = open(os.path.join(REPO, "app_data_manager.py"), encoding="utf-8").read() + \
        open(os.path.join(REPO, "data_manager", "candidate_registry.py"), encoding="utf-8").read()
    for t in ("candidates.html", "dashboard.html", "smmt_nowcast.html", "smmt_candidate.html"):
        src += open(os.path.join(REPO, "templates", t), encoding="utf-8").read()
    for s in ("908964", "908,964", "1023403", "1,023,403", "114,439", "114439", "smmt_nowcast_20261001_121056", "472869"):
        assert s not in src, s


def test_flask_workflow_and_refusals(builder, cand):
    import app_data_manager as A
    st = A.app.extensions["lct_smmt"]
    saved = st.builder
    st.builder = builder
    try:
        c = A.app.test_client()
        cid = cand["candidate_id"]
        assert c.get("/smmt-nowcast").status_code == 200
        assert c.get(f"/smmt-nowcast/{cid}").status_code == 200
        assert c.post("/api/promote", json={"run_id": cid}).status_code == 403
        assert c.post("/api/build-complete-candidate", json={"run_id": "x"}).status_code == 410
        assert c.get(f"/smmt-preview/{cid}/../app_data_manager.py").status_code == 404
        r = c.get(f"/smmt-preview/{cid}/project/output_processed/integration/smmt_nowcast_la.csv")
        assert r.status_code == 200 and b"SMMT_NOWCAST" in r.data
        m = c.get(f"/smmt-preview/{cid}/project/output_processed/integration/smmt_nowcast_manifest.json").get_json()
        assert m["candidate_id"] == cid and m["files"]["smmt_nowcast_la.csv"] == hashlib.sha256(r.data).hexdigest()
        r = c.get(f"/smmt-preview/{cid}/project/output_processed/integration/monthly_forecast_la.csv")
        assert r.status_code == 200 and b"SMMT_NOWCAST" not in r.data      # forecasts are served unchanged
    finally:
        st.builder = saved


# ---------------------------------------------------------------- proposed dashboard series (harness, no browser)
def test_patch_only_applies_to_the_pinned_dashboard():
    with pytest.raises(ValueError):
        patch.patched_dashboard("<html>not the dashboard</html>")
    text = open(os.path.join(REPO, "dashboard.html"), encoding="utf-8", newline="").read()
    assert patch.dashboard_sha256(text) == patch.PINNED_DASHBOARD_SHA256
    assert "showSmmt" in patch.patched_dashboard(text)


def _harness(tmp_path, html, mode, redirect=None):
    js = open(HARNESS, encoding="utf-8").read()
    old = "fs.readFileSync(path.join(REPO, 'dashboard.html'), 'utf8')"
    assert js.count(old) == 1
    h = tmp_path / "harness.js"
    h.write_text(js.replace(old, "fs.readFileSync(process.env.DASHBOARD_HTML, 'utf8')"), encoding="utf-8")
    page = tmp_path / f"dash_{abs(hash(html)) % 10**8}.html"
    page.write_text(html, encoding="utf-8", newline="")
    args = ["node", "--no-opt", "--no-sparkplug", str(h), mode]
    if redirect:
        rj = tmp_path / "redirect.json"
        rj.write_text(json.dumps(redirect), encoding="utf-8")
        args.append(str(rj))
    r = subprocess.run(args, cwd=REPO, capture_output=True, text=True, timeout=900, env={**os.environ, "DASHBOARD_HTML": str(page)})
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    return json.loads(r.stdout)


def _published_redirect(builder, cand, tmp_path):
    """Harness redirects that present the candidate exactly as smmt_publish publishes it (files + manifest)."""
    folder = builder.folder(cand["candidate_id"])
    mp = tmp_path / "smmt_nowcast_manifest.json"
    mp.write_text(json.dumps(builder.publication_manifest(cand["candidate_id"])), encoding="utf-8")
    red = {f"smmt_nowcast_{g}.csv": os.path.join(folder, f"smmt_nowcast_{g}.csv") for g in ("dno", "la", "laep")}
    red["smmt_nowcast_manifest.json"] = str(mp)
    return red


@pytest.fixture(scope="module")
def html_pair():
    text = open(os.path.join(REPO, "dashboard.html"), encoding="utf-8", newline="").read()
    return text, patch.patched_dashboard(text)


@pytest.mark.parametrize("mode", ["ev", "ev_local", "path", "nonev"])
def test_patched_dashboard_is_unchanged_without_smmt_rows(tmp_path, html_pair, mode):
    a = _harness(tmp_path, html_pair[0], mode)
    b = _harness(tmp_path, html_pair[1], mode)
    assert a["results"] == b["results"] and a["startup"] == b["startup"]


@pytest.mark.parametrize("mode", ["ev", "ev_local", "path"])
def test_patched_dashboard_shows_smmt_series_from_the_latest_actual(tmp_path, html_pair, builder, cand, mode):
    folder = builder.folder(cand["candidate_id"])
    redirect = _published_redirect(builder, cand, tmp_path)
    out = _harness(tmp_path, html_pair[1], mode, redirect)
    first = cand["nowcast_months"][0]
    last = cand["latest_nowcast_month"]
    latest_actual = cand["baseline_period"]
    quarter_ends = [m for m in cand["nowcast_months"] if int(m[5:7]) % 3 == 0]
    assert out["results"], "no snapshots"
    for snap in out["results"]:
        plots = snap["plots"]
        stock = {t["name"]: t for t in plots["stockChart"]["traces"]}
        cum = {t["name"]: t for t in plots["evCumulativeChart"]["traces"]}
        bars = {t["name"]: t for t in plots["monthlyChart"]["traces"]}
        s = stock["SMMT provisional nowcast"]                     # present in every selection (no disappearing line)
        assert s["x"][0] == f"{latest_actual}-01" and s["x"][1] == f"{first}-01" and s["x"][-1] == f"{last}-01"
        actual = stock["Estimated EV stock"]
        assert actual["x"][-1] == f"{latest_actual}-01" and s["y"][0] == actual["y"][-1]     # starts at the actual
        assert all(y2 >= y1 for y1, y2 in zip(s["y"], s["y"][1:]))
        c = cum["SMMT provisional nowcast"]
        assert c["y"][0] == cum["Estimated cumulative net stock additions"]["y"][-1]
        for tr in plots["stockChart"]["traces"] + plots["evCumulativeChart"]["traces"]:
            assert max(tr["x"]) <= f"{last}-01"                     # nothing beyond the supplied SMMT months
        b = bars["SMMT provisional nowcast"]
        shown = [x for x, y in zip(b["x"], b["y"]) if y is not None]
        assert shown == [f"{q}-01" for q in quarter_ends]
        assert "provisional estimate, not an observation" in snap["caveats"][2]


def test_patched_dashboard_values_match_candidate_rows(tmp_path, html_pair, builder, cand):
    folder = builder.folder(cand["candidate_id"])
    redirect = _published_redirect(builder, cand, tmp_path)
    out = _harness(tmp_path, html_pair[1], "ev", redirect)
    geo, lsoa, s = _files(builder, cand)
    dno = geo["dno"]
    for snap in out["results"]:
        _, area, sub = snap["label"].split("|")
        sel = dno if area == "UKPN Total" else dno[dno["DNO"] == area]
        if sub != "Total":
            sel = sel[sel["EV_Type"] == sub]
        expect = sel.groupby("period")["value"].sum()
        tr = {t["name"]: t for t in snap["plots"]["stockChart"]["traces"]}["SMMT provisional nowcast"]
        got = dict(zip([x[:7] for x in tr["x"][1:]], tr["y"][1:]))
        assert got == {p: int(v) for p, v in expect.items()}
