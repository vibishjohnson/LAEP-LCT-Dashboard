"""Self-contained tests for the guarded dashboard publisher (synthetic data in temporary directories only).

Run from the repository root:
    python -m pytest integration_runner/tests -p no:cacheprovider -q
"""
import hashlib
import json
import os
import sys
from pathlib import Path

import pandas as pd
import pytest

RUNNER_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNNER_ROOT))
from dashboard_integration import publish_guard as guard  # noqa: E402
from dashboard_integration.config import local_path  # noqa: E402
import provenance  # noqa: E402

PKG = RUNNER_ROOT / "dashboard_integration"
OPERATIONAL = local_path("operational_runner_dir", "C1_RUNNER_DIR")   # skipped when not configured
GEOS = {"monthly_forecast_dno.csv": ("DNO", ["EPN", "LPN", "SPN"]),
        "monthly_forecast_la.csv": ("LAD22CD", ["E06000031", "E09000033", "E07000008", "E06000055"]),
        "monthly_forecast_laep.csv": ("LAEP", ["Norwich LAEP", "Peterborough LAEP"])}
PERIODS = pd.period_range("2025-03", "2026-02", freq="M").strftime("%Y-%m").tolist()
SERIES = [("DFES", "HolisticTransition"), ("NESO", "HolisticTransition"), ("NESO", "HolisticTransitionBasic")]
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
norm = lambda p: hashlib.sha256(Path(p).read_bytes().replace(b'\r\n', b'\n')).hexdigest()   # checkout-EOL independent


def forecast(name):
    geo, values = GEOS[name]
    rows = [{geo: g, "value": 100.0 + i, "time_grain": "month", "tech_type": "EV", "EV_Type": fuel, "forecast_source": src,
             "forecast_scenario": scen, "metric": "stock", "observation_source": src, "period": p}
            for i, p in enumerate(PERIODS) for g in values for src, scen in SERIES for fuel in ("BEV", "PHEV")]
    return pd.DataFrame(rows)


@pytest.fixture
def dirs(tmp_path):
    out, pub = tmp_path / "outputs", tmp_path / "publish"
    out.mkdir()
    pub.mkdir()
    for name in GEOS:
        forecast(name).to_csv(out / name, index=False)
        forecast(name).to_csv(pub / name, index=False)
    for d in (out, pub):
        (d / "geography_lookup.csv").write_text("LSOA21CD,LAEP\nE01,Norwich LAEP\n")
        (d / "metadata.json").write_text(json.dumps({"latest_actual_period": "2026-03"}))
    (out / "run_report.md").write_text("# report\n")
    for name in guard.EV_ACTUALS:
        (out / name).write_text("period,value\n2011-12,314\n")            # legacy actuals built for QA only
        (pub / name).write_text("period,value\n2025-03,1\n2026-03,2\n")    # the published (C1) actuals
    return out, pub, tmp_path / "backups"


def files(d):
    return {p.name: sha(p) for p in Path(d).iterdir() if p.is_file()} if Path(d).exists() else {}


def test_publishes_forecasts_and_never_ev_actuals(dirs):
    out, pub, backups = dirs
    before = files(pub)
    report = guard.publish(out, pub, backups)
    after = files(pub)
    assert sorted(report["published"]) == sorted(guard.PUBLISH_FILES)
    assert not any(n.startswith("ev_actuals_") for n in guard.PUBLISH_FILES)
    for name in guard.EV_ACTUALS:
        assert after[name] == before[name]
    for name in guard.PUBLISH_FILES:
        assert after[name] == sha(out / name)
    manifest = json.loads((Path(report["backup_dir"]) / "manifest.json").read_text())
    assert manifest["files_sha256"]["monthly_forecast_dno.csv"] == before["monthly_forecast_dno.csv"]
    assert not [p for p in pub.iterdir() if p.name.endswith(".publish_tmp")]


CASES = {
    "duplicate keys": ("monthly_forecast_dno.csv", lambda d: pd.concat([d, d.head(2)]), "duplicate keys"),
    "missing series": ("monthly_forecast_la.csv", lambda d: d[d.forecast_scenario != "HolisticTransitionBasic"], "missing series"),
    "missing month": ("monthly_forecast_laep.csv", lambda d: d[~((d.LAEP == "Norwich LAEP") & (d.period == "2025-07"))], "incomplete coverage"),
    "actual rows": ("monthly_forecast_dno.csv", lambda d: d.assign(observation_source=["ACTUAL"] + ["DFES"] * (len(d) - 1)), "not DFES/NESO"),
    "negative": ("monthly_forecast_dno.csv", lambda d: d.assign(value=[-1.0] + list(d.value[1:])), "negative"),
    "geography lost": ("monthly_forecast_la.csv", lambda d: d[d.LAD22CD != "E09000033"], "geographies would disappear"),
    "range shrinks": ("monthly_forecast_dno.csv", lambda d: d[d.period <= "2026-01"], "period range shrinks"),
}


@pytest.mark.parametrize("case", list(CASES))
def test_invalid_forecasts_refused_with_nothing_written(dirs, case):
    out, pub, backups = dirs
    name, fn, expect = CASES[case]
    fn(pd.read_csv(out / name)).to_csv(out / name, index=False)
    before = files(pub)
    with pytest.raises(guard.PublishRefused, match=expect):
        guard.publish(out, pub, backups)
    assert files(pub) == before and not backups.exists()


def test_existing_nowcast_rows_are_not_dropped_silently(dirs, monkeypatch):
    out, pub, backups = dirs
    d = pd.read_csv(pub / "monthly_forecast_dno.csv")
    # Nowcast months sit inside the forecast horizon in practice (Apr-Aug 2026 within 2025-03..2051-03). Known
    # limitation: nowcast rows beyond the horizon would be counted by the shrinkage check and refused (fail closed).
    smmt = d.head(2).assign(forecast_source="SMMT_NOWCAST", forecast_scenario="Nowcast", period="2025-06")
    pd.concat([d, smmt]).to_csv(pub / "monthly_forecast_dno.csv", index=False)
    before = files(pub)
    with pytest.raises(guard.PublishRefused, match="drop SMMT_NOWCAST"):
        guard.publish(out, pub, backups)
    assert files(pub) == before
    monkeypatch.setenv("C1_ALLOW_DROP_NOWCAST", "1")
    guard.publish(out, pub, backups)


def test_geography_change_and_metadata_mismatch_refused(dirs):
    out, pub, backups = dirs
    (out / "geography_lookup.csv").write_text("LSOA21CD,LAEP\nE02,Other\n")
    with pytest.raises(guard.PublishRefused, match="geography_lookup.csv would change"):
        guard.publish(out, pub, backups)
    (out / "geography_lookup.csv").write_text("LSOA21CD,LAEP\nE01,Norwich LAEP\n")
    (out / "metadata.json").write_text(json.dumps({"latest_actual_period": "2026-06"}))
    with pytest.raises(guard.PublishRefused, match="latest_actual_period"):
        guard.publish(out, pub, backups)


def test_ev_actuals_can_never_be_added_to_the_publish_list(dirs, monkeypatch):
    out, pub, backups = dirs
    monkeypatch.setattr(guard, "PUBLISH_FILES", guard.PUBLISH_FILES + ("ev_actuals_dno.csv",))
    before = files(pub)
    with pytest.raises(guard.PublishRefused, match="EV actuals"):
        guard.publish(out, pub, backups)
    assert files(pub) == before


def test_runner_publishes_only_through_the_guard():
    src = (PKG / "run_integration.py").read_text(encoding="utf-8")
    body = src[src.index("def run() -> dict:"):]
    assert body.count("DASHBOARD_INTEGRATION_PUBLISH") == 1
    assert "publish(OUTPUT_DIR, DASHBOARD_INTEGRATION_PUBLISH)" in body and "copy2" not in body
    assert "from .publish_guard import PublishRefused, publish" in src


def test_source_manifest_matches_files():
    manifest = json.loads((RUNNER_ROOT / "SOURCE_MANIFEST.json").read_text())
    assert {p.name: norm(p) for p in sorted(PKG.glob("*.py"))} == provenance.expected_repository_hashes(manifest)
    assert set(manifest["repository_copy_sanitisation"]["files"]) == {"config.py", "hp_config.py", "hp_qa.py"}
    assert provenance.problems() == []
    assert norm(RUNNER_ROOT / "original" / "run_integration.py") == manifest["original_sha256_eol_normalised"]["run_integration.py"]
    changed = {n for n in manifest["current_sha256"] if manifest["original_sha256"].get(n) != manifest["current_sha256"][n]}
    assert changed == {"run_integration.py", "publish_guard.py"}


def test_operational_runner_matches_this_copy():
    if not OPERATIONAL.exists():
        pytest.skip("operational runner folder not present on this machine")
    manifest = json.loads((RUNNER_ROOT / "SOURCE_MANIFEST.json").read_text())
    for name, digest in manifest["current_sha256_eol_normalised"].items():
        assert norm(OPERATIONAL / name) == digest, name
    for name, digest in manifest["current_sha256"].items():          # the operational files are the exact recorded bytes
        assert sha(OPERATIONAL / name) == digest, name
    assert provenance.problems(OPERATIONAL) == []                    # and differ from this copy only in the recorded hunks
