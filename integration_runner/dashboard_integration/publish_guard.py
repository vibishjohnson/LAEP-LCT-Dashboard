"""Guarded publishing of dashboard integration files (C1 safeguard).

The dashboard's EV actuals (ev_actuals_dno/la/laep.csv) are published only by the C1 release tool in the
LAEP LCT Dashboard repository. This module publishes the forecast integration files and refuses anything unsafe:

  * never writes any ev_actuals_*.csv, and verifies the published EV actuals are byte-identical afterwards;
  * validates every monthly forecast before anything is written: required columns, only DFES/NESO forecast rows
    (no actuals), all of DFES HolisticTransition and NESO HolisticTransition/HolisticTransitionBasic for BEV and
    PHEV, no duplicate keys, complete monthly coverage for every geography, non-negative numeric values, and no
    shrinkage of the currently published period range or geography set;
  * refuses to drop SMMT_NOWCAST rows already published (set C1_ALLOW_DROP_NOWCAST=1 deliberately to override);
  * refuses a changed geography_lookup.csv, which C1's LAEP aggregation depends on (set
    C1_ALLOW_GEOGRAPHY_CHANGE=1 only after C1 has been re-validated against the new lookup);
  * requires metadata.json latest_actual_period to match the published EV actuals;
  * backs up every file it replaces (with hashes) outside the dashboard before writing, and replaces files one at a
    time via a verified temporary copy and os.replace.

Validation failures raise PublishRefused before any file is written.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd

EV_ACTUALS = ("ev_actuals_dno.csv", "ev_actuals_la.csv", "ev_actuals_laep.csv")
PUBLISH_FILES = (
    "monthly_forecast_dno.csv",
    "monthly_forecast_la.csv",
    "monthly_forecast_laep.csv",
    "geography_lookup.csv",
    "metadata.json",
    "run_report.md",
)
FORECAST_GEOGRAPHY = {
    "monthly_forecast_dno.csv": "DNO",
    "monthly_forecast_la.csv": "LAD22CD",
    "monthly_forecast_laep.csv": "LAEP",
}
REQUIRED_SERIES = (
    ("DFES", "HolisticTransition"),
    ("NESO", "HolisticTransition"),
    ("NESO", "HolisticTransitionBasic"),
)
FUELS = ("BEV", "PHEV")


class PublishRefused(RuntimeError):
    """Raised when outputs fail validation; nothing has been written."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_forecast(path: Path, name: str, current: Path | None = None) -> list[str]:
    """Return a list of problems with a monthly forecast file (empty when valid)."""
    geo = FORECAST_GEOGRAPHY[name]
    problems: list[str] = []
    try:
        df = pd.read_csv(path, low_memory=False)
    except Exception as exc:  # noqa: BLE001
        return [f"{name}: unreadable ({exc})"]
    needed = [geo, "value", "EV_Type", "forecast_source", "forecast_scenario", "observation_source", "period"]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        return [f"{name}: missing columns {missing}"]
    if df.empty:
        return [f"{name}: empty"]
    if not df["forecast_source"].isin(["DFES", "NESO"]).all() or not df["observation_source"].isin(["DFES", "NESO"]).all():
        problems.append(f"{name}: contains rows that are not DFES/NESO forecasts (actuals or other sources)")
    for source, scenario in REQUIRED_SERIES:
        for fuel in FUELS:
            n = ((df["forecast_source"] == source) & (df["forecast_scenario"] == scenario) & (df["EV_Type"] == fuel)).sum()
            if n == 0:
                problems.append(f"{name}: missing series {source}/{scenario}/{fuel}")
    key = ["period", geo, "forecast_source", "forecast_scenario", "EV_Type"]
    if df[key].isna().any().any():
        problems.append(f"{name}: null key values")
    dups = int(df.duplicated(key).sum())
    if dups:
        problems.append(f"{name}: {dups} duplicate keys")
    values = pd.to_numeric(df["value"], errors="coerce")
    if values.isna().any() or (values < 0).any():
        problems.append(f"{name}: non-numeric, missing or negative values")
    periods = sorted(df["period"].astype(str).unique())
    try:
        expected = pd.period_range(periods[0], periods[-1], freq="M").strftime("%Y-%m").tolist()
    except Exception:  # noqa: BLE001
        return problems + [f"{name}: invalid period values"]
    if periods != expected:
        problems.append(f"{name}: period range {periods[0]}..{periods[-1]} has gaps")
    geos = set(df[geo].astype(str))
    counts = df.groupby(["forecast_source", "forecast_scenario", "EV_Type", geo]).size()
    complete = len(counts) == len(REQUIRED_SERIES) * len(FUELS) * len(geos) and (counts == len(expected)).all()
    if not complete:
        problems.append(f"{name}: incomplete coverage (every series x geography must have all {len(expected)} months)")
    if current is not None and current.exists():
        cur = pd.read_csv(current, usecols=["period", geo], low_memory=False)
        if str(cur["period"].min()) < periods[0] or str(cur["period"].max()) > periods[-1]:
            problems.append(f"{name}: period range shrinks versus the published file")
        lost = set(cur[geo].astype(str)) - geos
        if lost:
            problems.append(f"{name}: {len(lost)} published geographies would disappear")
    return problems


def _published_nowcast_rows(publish_dir: Path) -> dict[str, int]:
    out = {}
    for name in FORECAST_GEOGRAPHY:
        path = publish_dir / name
        if path.exists():
            n = int((pd.read_csv(path, usecols=["forecast_source"])["forecast_source"] == "SMMT_NOWCAST").sum())
            if n:
                out[name] = n
    return out


def check(output_dir: Path, publish_dir: Path) -> list[str]:
    """All validation, no writes. Returns a list of problems."""
    output_dir, publish_dir = Path(output_dir), Path(publish_dir)
    if any(name in EV_ACTUALS or name.startswith("ev_actuals_") for name in PUBLISH_FILES):
        return ["publish list includes EV actuals; EV actuals are published only by the C1 release tool"]
    problems = [f"missing output {n}" for n in PUBLISH_FILES if not (output_dir / n).is_file()]
    if problems:
        return problems
    for name in FORECAST_GEOGRAPHY:
        problems += validate_forecast(output_dir / name, name, publish_dir / name)
    nowcast = _published_nowcast_rows(publish_dir)
    if nowcast and os.environ.get("C1_ALLOW_DROP_NOWCAST") != "1":
        problems.append(f"publishing would drop SMMT_NOWCAST rows already published {nowcast}; "
                        "re-promote the nowcast afterwards and set C1_ALLOW_DROP_NOWCAST=1 deliberately")
    geo_new, geo_cur = output_dir / "geography_lookup.csv", publish_dir / "geography_lookup.csv"
    if geo_cur.exists() and _sha256(geo_new) != _sha256(geo_cur) and os.environ.get("C1_ALLOW_GEOGRAPHY_CHANGE") != "1":
        problems.append("geography_lookup.csv would change; C1 LAEP aggregation depends on it. Re-validate C1 first, "
                        "then set C1_ALLOW_GEOGRAPHY_CHANGE=1 deliberately")
    try:
        meta = json.loads((output_dir / "metadata.json").read_text(encoding="utf-8"))
    except ValueError as exc:
        return problems + [f"metadata.json unreadable: {exc}"]
    published_actuals = publish_dir / "ev_actuals_dno.csv"
    if published_actuals.exists():
        latest = str(pd.read_csv(published_actuals, usecols=["period"])["period"].max())
        if meta.get("latest_actual_period") != latest:
            problems.append(f"metadata.json latest_actual_period {meta.get('latest_actual_period')} does not match the "
                            f"published EV actuals ({latest})")
    return problems


def publish(output_dir: Path, publish_dir: Path, backup_root: Path | None = None) -> dict:
    """Validate, back up and publish the forecast integration files. Never touches EV actuals."""
    output_dir, publish_dir = Path(output_dir), Path(publish_dir)
    problems = check(output_dir, publish_dir)
    if problems:
        raise PublishRefused("; ".join(problems))
    publish_dir.mkdir(parents=True, exist_ok=True)
    ev_before = {n: _sha256(publish_dir / n) for n in EV_ACTUALS if (publish_dir / n).exists()}

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_dir = Path(backup_root or output_dir.parent / "publish_backups") / f"publish_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backed_up = {}
    for name in PUBLISH_FILES:
        current = publish_dir / name
        if current.exists():
            shutil.copy2(current, backup_dir / name)
            if _sha256(backup_dir / name) != _sha256(current):
                raise PublishRefused(f"backup verification failed for {name}; nothing was published")
            backed_up[name] = _sha256(backup_dir / name)
    (backup_dir / "manifest.json").write_text(json.dumps({"publish_dir": str(publish_dir), "files_sha256": backed_up},
                                                         indent=2), encoding="utf-8")

    published = {}
    for name in PUBLISH_FILES:
        src, dst = output_dir / name, publish_dir / name
        tmp = publish_dir / f".{name}.publish_tmp"
        shutil.copy2(src, tmp)
        if _sha256(tmp) != _sha256(src):
            os.remove(tmp)
            raise PublishRefused(f"copy verification failed for {name}; published so far: {sorted(published)}; "
                                 f"originals are in {backup_dir}")
        os.replace(tmp, dst)
        published[name] = _sha256(dst)

    ev_after = {n: _sha256(publish_dir / n) for n in EV_ACTUALS if (publish_dir / n).exists()}
    if ev_after != ev_before:
        raise PublishRefused("EV actuals changed during publishing; investigate immediately")
    return {"published": published, "backup_dir": str(backup_dir), "ev_actuals_unchanged": True}
