"""
SMMT nowcast live-integration design, exercised end to end in disposable environments (the live repository is only read):
publication into versioned integration files, the narrow dashboard.html re-pin, and the patched dashboard loading the
published files at every geographic level (Node harness; the real-browser run is in the acceptance evidence).
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from data_manager import smmt_dashboard_patch as patch  # noqa: E402
from data_manager import smmt_publish as pub  # noqa: E402
from data_manager.smmt_candidate_builder import INTEGRATION_REL, SMmtCandidateBuilder  # noqa: E402
from data_manager.c1_refresh import config as C, release_readiness as rr  # noqa: E402
import c1_release as rel  # noqa: E402
import release_config as cfg  # noqa: E402
import repin_dashboard as rd  # noqa: E402
from data_manager.db import DB_PATH  # noqa: E402

HARNESS = os.path.join(REPO, "experiments", "ev_redistribution_c1", "dashboard_regression", "dashboard_harness.js")
sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
LIVE = [os.path.join(REPO, *INTEGRATION_REL.split("/"), n) for n in os.listdir(os.path.join(REPO, *INTEGRATION_REL.split("/")))] + \
       [os.path.join(REPO, "dashboard.html"), DB_PATH]


@pytest.fixture(scope="module", autouse=True)
def live_untouched():
    before = {p: sha(p) for p in LIVE if os.path.isfile(p)}
    yield
    assert {p: sha(p) for p in LIVE if os.path.isfile(p)} == before
    assert not any(n.startswith("smmt_nowcast") for n in os.listdir(os.path.join(REPO, *INTEGRATION_REL.split("/"))))


@pytest.fixture(scope="module")
def env():
    d = os.path.join(C.short_tmp(), f"smmtint-{os.getpid()}")
    rr.build_chain_env(d)
    os.makedirs(os.path.join(d, "ev"), exist_ok=True)
    shutil.copyfile(os.path.join(REPO, "ev", "df_VEH0135.csv"), os.path.join(d, "ev", "df_VEH0135.csv"))
    yield d
    rr.remove_env(d)


@pytest.fixture(scope="module")
def cand(env):
    b = SMmtCandidateBuilder(env, db_path=DB_PATH)
    m = b.generate()
    assert m["status"] == "VALIDATED", m.get("errors")
    return b, m


def integ(env, n=""):
    return os.path.join(env, *INTEGRATION_REL.split("/"), n)


# ---------------------------------------------------------------- narrow re-pin
def test_dashboard_repin_rule_is_exactly_the_patch():
    text = open(os.path.join(REPO, "dashboard.html"), encoding="utf-8", newline="").read()
    patched = patch.patched_dashboard(text)
    assert hashlib.sha256(patched.encode("utf-8")).hexdigest() == patch.PATCHED_DASHBOARD_SHA256
    assert rel.DASHBOARD_REPIN == {(patch.PINNED_DASHBOARD_SHA256, patch.PATCHED_DASHBOARD_SHA256),
                                   (patch.PATCHED_DASHBOARD_SHA256, patch.PINNED_DASHBOARD_SHA256)}
    assert cfg.PINNED_SHA256["dashboard.html"] == patch.PINNED_DASHBOARD_SHA256
    assert patch.original_dashboard(patched) == text                  # exact inverse, byte for byte


def test_dashboard_apply_then_rollback_with_reverse_repin(env):
    dash = os.path.join(env, "dashboard.html")
    original = open(dash, "rb").read()
    pinlog = Path(env).joinpath(*rel.PIN_LOG_REL.split("/"))
    try:
        patch.write_dashboard(env, "apply")
        rd.repin(env, rd.plan(env)["approval_token"])
        assert rel.effective_pins(env)["dashboard.html"] == patch.PATCHED_DASHBOARD_SHA256
        patch.write_dashboard(env, "revert")
        assert open(dash, "rb").read() == original
        p = rd.plan(env)
        assert p["valid"] and p["new_sha256"] == patch.PINNED_DASHBOARD_SHA256
        rd.repin(env, p["approval_token"])
        assert rel.effective_pins(env)["dashboard.html"] == patch.PINNED_DASHBOARD_SHA256
        assert [e["path"] for e in rel.read_pin_log(env)] == ["dashboard.html", "dashboard.html"]
        assert rel.verify_c1(env)["ok"]
    finally:
        open(dash, "wb").write(original)
        if pinlog.exists():
            pinlog.unlink()


def test_write_dashboard_refused_on_live_repository():
    with pytest.raises(PermissionError):
        patch.write_dashboard(REPO, "apply")


def _forged_entry(env, new):
    e = {"seq": 0, "path": "dashboard.html", "old_sha256": cfg.PINNED_SHA256["dashboard.html"], "new_sha256": new, "prev_entry_sha256": ""}
    e["entry_sha256"] = rel.seal_entry(e)
    p = Path(env).joinpath(*rel.PIN_LOG_REL.split("/"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([e]))
    return p


def test_pin_log_refuses_other_dashboard_versions(tmp_path):
    root = tmp_path / "r"
    root.mkdir()
    _forged_entry(root, "1" * 64)
    with pytest.raises(rel.PinLogInvalid):
        rel.effective_pins(root)
    _forged_entry(root, patch.PATCHED_DASHBOARD_SHA256)
    assert rel.effective_pins(root)["dashboard.html"] == patch.PATCHED_DASHBOARD_SHA256


def test_repin_dashboard_tool(env):
    dash = os.path.join(env, "dashboard.html")
    original = open(dash, encoding="utf-8", newline="").read()
    pinlog = Path(env).joinpath(*rel.PIN_LOG_REL.split("/"))
    try:
        with open(dash, "w", encoding="utf-8", newline="") as f:
            f.write(patch.patched_dashboard(original) + "<!-- extra -->")
        p = rd.plan(env)
        assert not p["valid"] and "not the approved" in p["problems"][0]
        with pytest.raises(rd.RepinRefused):
            rd.repin(env, p["approval_token"])
        assert not pinlog.exists()
        with open(dash, "w", encoding="utf-8", newline="") as f:
            f.write(patch.patched_dashboard(original))
        p = rd.plan(env)
        assert p["valid"], p["problems"]
        with pytest.raises(rel.ConfirmationMismatch):
            rd.repin(env, "REPIN dashboard.html wrong")
        e = rd.repin(env, p["approval_token"])
        assert e["new_sha256"] == patch.PATCHED_DASHBOARD_SHA256
        assert rel.verify_c1(env)["ok"]
    finally:
        with open(dash, "w", encoding="utf-8", newline="") as f:
            f.write(original)
        if pinlog.exists():
            pinlog.unlink()
    assert rel.verify_c1(env)["ok"]


def test_repin_dashboard_refused_on_live_repository():
    with pytest.raises(rel.LiveReleaseBlocked):
        rd.repin(REPO, "REPIN anything")


# ---------------------------------------------------------------- publication
def test_publish_refusals(env, cand, monkeypatch):
    b, m = cand
    with pytest.raises(pub.PublishRefused):
        pub.publish(env, m["candidate_id"], "PUBLISH SMMT wrong", db_path=DB_PATH)
    with pytest.raises(pub.PublishRefused):
        pub.publish(REPO, m["candidate_id"], "x")                                    # live repository
    real = SMmtCandidateBuilder.smmt_entries
    monkeypatch.setattr(SMmtCandidateBuilder, "smmt_entries",
                        lambda self: [dict(e, bev_cars=e["bev_cars"] + 1) for e in real(self)])
    p = pub.plan(env, m["candidate_id"], DB_PATH)
    assert not p["valid"] and "STALE" in p["problems"][0]
    assert not os.path.exists(integ(env, pub.MANIFEST))


def test_publish_and_withdraw(env, cand):
    b, m = cand
    before = {n: sha(integ(env, n)) for n in os.listdir(integ(env)) if os.path.isfile(integ(env, n))}
    p = pub.plan(env, m["candidate_id"], DB_PATH)
    man = pub.publish(env, m["candidate_id"], p["approval_token"], db_path=DB_PATH)
    assert man["candidate_id"] == m["candidate_id"] and man["baseline_period"] == m["baseline_period"]
    for n in pub.PUBLISHED_FILES:
        assert sha(integ(env, n)) == man["files"][n] == sha(os.path.join(b.folder(m["candidate_id"]), n))
    after = {n: sha(integ(env, n)) for n in before}
    assert after == before                                            # C1 actuals, forecasts, HP files untouched
    assert rel.verify_c1(env)["ok"]
    rec = pub.withdraw(env, f"WITHDRAW SMMT {m['candidate_id']}")
    assert rec["action"] == "withdraw" and not any(os.path.exists(integ(env, n)) for n in pub.PUBLISHED_FILES + (pub.MANIFEST,))


# ---------------------------------------------------------------- patched dashboard against published files (harness)
def _harness(env, mode, html):
    js = open(HARNESS, encoding="utf-8").read().replace("fs.readFileSync(path.join(REPO, 'dashboard.html'), 'utf8')",
                                                       "fs.readFileSync(process.env.DASHBOARD_HTML, 'utf8')")
    h = os.path.join(env, "harness_tmp.js")
    page = os.path.join(env, "dashboard_tmp.html")
    open(h, "w", encoding="utf-8").write(js)
    open(page, "w", encoding="utf-8", newline="").write(html)
    r = subprocess.run(["node", "--no-opt", "--no-sparkplug", h, mode], cwd=env, capture_output=True, text=True, timeout=900,
                       env={**os.environ, "DASHBOARD_HTML": page})
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
    return json.loads(r.stdout)


@pytest.fixture(scope="module")
def pages():
    text = open(os.path.join(REPO, "dashboard.html"), encoding="utf-8", newline="").read()
    return text, patch.patched_dashboard(text)


@pytest.fixture
def published(env, cand):
    b, m = cand
    man = pub.publish(env, m["candidate_id"], pub.plan(env, m["candidate_id"], DB_PATH)["approval_token"], db_path=DB_PATH)
    yield b, m, man
    pub.withdraw(env, f"WITHDRAW SMMT {m['candidate_id']}")


@pytest.mark.parametrize("mode", ["ev", "ev_local", "path"])
def test_published_nowcast_at_every_level(env, pages, published, mode):
    b, m, man = published
    out = _harness(env, mode, pages[1])
    last, base = m["latest_nowcast_month"], m["baseline_period"]
    for snap in out["results"]:
        stock = {t["name"]: t for t in snap["plots"]["stockChart"]["traces"]}
        cum = {t["name"]: t for t in snap["plots"]["evCumulativeChart"]["traces"]}
        s, a = stock["SMMT provisional nowcast"], stock["Estimated EV stock"]
        assert s["x"][0] == a["x"][-1] == f"{base}-01" and s["y"][0] == a["y"][-1] and s["x"][-1] == f"{last}-01"
        assert cum["SMMT provisional nowcast"]["y"][0] == cum["Estimated cumulative net stock additions"]["y"][-1]
        assert "provisional estimate, not an observation" in snap["caveats"][2]


@pytest.mark.parametrize("mode", ["ev", "ev_local", "path", "nonev"])
def test_absent_nowcast_falls_back_to_unchanged_page(env, pages, mode):
    assert not os.path.exists(integ(env, pub.MANIFEST))
    a, b = _harness(env, mode, pages[0]), _harness(env, mode, pages[1])
    assert a["results"] == b["results"] and a["startup"] == b["startup"]


def test_nonev_views_unchanged_with_nowcast_published(env, pages, published):
    a = _harness(env, "nonev", pages[0])
    b = _harness(env, "nonev", pages[1])
    assert a["results"] == b["results"]


def test_stale_or_altered_nowcast_is_not_shown(env, pages, published):
    b, m, man = published
    saved = {n: open(integ(env, n), "rb").read() for n in pub.PUBLISHED_FILES + (pub.MANIFEST,)}
    try:
        stale = dict(man, baseline_period="2025-12")
        open(integ(env, pub.MANIFEST), "w", encoding="utf-8").write(json.dumps(stale))
        out = _harness(env, "ev", pages[1])
        for snap in out["results"]:
            assert all(t["name"] != "SMMT provisional nowcast" for t in snap["plots"]["stockChart"]["traces"])
            assert "must be regenerated" in snap["caveats"][2]
        open(integ(env, pub.MANIFEST), "wb").write(saved[pub.MANIFEST])
        with open(integ(env, "smmt_nowcast_la.csv"), "ab") as f:
            f.write(b"E06000031,Peterborough,999999,month,EV,BEV,SMMT_NOWCAST,x,,,stock,SMMT,2026-08,2026-08-31,False,False,,\n")
        out = _harness(env, "ev_local", pages[1])
        la = [s for s in out["results"] if s["label"].startswith("LA|")]
        for snap in la:
            assert all(t["name"] != "SMMT provisional nowcast" for t in snap["plots"]["stockChart"]["traces"])
            assert "does not match the published manifest" in snap["caveats"][2]
        laep = [s for s in out["results"] if s["label"].startswith("LAEP|")]
        assert all(any(t["name"] == "SMMT provisional nowcast" for t in s["plots"]["stockChart"]["traces"]) for s in laep)
    finally:
        for n, data in saved.items():
            open(integ(env, n), "wb").write(data)
