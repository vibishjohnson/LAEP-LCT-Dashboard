"""Disposable fixtures for promotion/rollback tests. Everything is created under a temporary directory.

Builds a small project tree shaped like production (managed files, C1-protected EV actuals, HP files, unrelated
files) and staged candidates. Never touches the real project/output_processed or project/production_backups.
"""

import hashlib
import json
import os
import subprocess
import sys

import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FORECAST_COLUMNS = ["DNO", "value", "time_grain", "tech_type", "EV_Type", "forecast_source", "forecast_scenario",
                    "dfes_pathway", "building_block_id", "metric", "observation_source", "period", "period_end",
                    "is_anchor", "is_interpolated", "source_year", "anchor_date"]
PERIODS = ["2025-03", "2025-04", "2025-05", "2025-06", "2025-07", "2025-08"]
SERIES = [("DFES", "HolisticTransition"), ("NESO", "HolisticTransition"), ("NESO", "HolisticTransitionBasic")]
EV_ACTUALS = ("ev_actuals_dno.csv", "ev_actuals_la.csv", "ev_actuals_laep.csv")
HP_FILES = ("hp_actuals_dno.csv", "hp_actuals_la.csv", "hp_actuals_laep.csv", "hp_monthly_forecast_dno.csv",
            "hp_monthly_forecast_la.csv", "hp_monthly_forecast_laep.csv", "hp_metadata.json")


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def tree(root):
    out = {}
    for d, _, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            out[os.path.relpath(p, root).replace(os.sep, "/")] = sha(p)
    return out


def forecast_frame(smmt=False):
    rows = []
    for i, period in enumerate(PERIODS):
        for dno_i, dno in enumerate(("EPN", "LPN", "SPN")):
            for s_i, (source, scenario) in enumerate(SERIES):
                for fuel in ("BEV", "PHEV"):
                    rows.append({"DNO": dno, "value": 1000.123456789 * (1 + i) + 37.5 * dno_i + 11 * s_i + (5 if fuel == "PHEV" else 0),
                                 "time_grain": "month", "tech_type": "EV", "EV_Type": fuel, "forecast_source": source,
                                 "forecast_scenario": scenario, "dfes_pathway": "", "building_block_id": "", "metric": "stock",
                                 "observation_source": source, "period": period, "period_end": f"{period}-28",
                                 "is_anchor": False, "is_interpolated": True, "source_year": "", "anchor_date": ""})
    if smmt:
        for period in ("2026-04", "2026-05"):
            for dno in ("EPN", "LPN", "SPN"):
                for fuel in ("BEV", "PHEV"):
                    rows.append({**rows[0], "DNO": dno, "EV_Type": fuel, "value": 500.0, "forecast_source": "SMMT_NOWCAST",
                                 "forecast_scenario": "Nowcast", "observation_source": "SMMT_NOWCAST", "period": period,
                                 "period_end": f"{period}-30", "is_interpolated": False})
    return pd.DataFrame(rows, columns=FORECAST_COLUMNS)


def write_csv(frame, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    frame.to_csv(path, index=False, lineterminator="\r\n")


def make_project(root):
    """Production-shaped tree. EV actuals stand in for the published C1 files."""
    out = os.path.join(root, "project", "output_processed")
    integ = os.path.join(out, "integration")
    os.makedirs(integ, exist_ok=True)
    for name in EV_ACTUALS:
        with open(os.path.join(integ, name), "wb") as f:
            f.write(f"period,EV_Type,value\r\n2026-03,BEV,{len(name)}\r\n".encode())
    for name in ("monthly_forecast_dno.csv", "monthly_forecast_la.csv", "monthly_forecast_laep.csv"):
        write_csv(forecast_frame(), os.path.join(integ, name))
    with open(os.path.join(integ, "metadata.json"), "w") as f:
        json.dump({"latest_actual_period": "2026-03", "qa_pass": True}, f, indent=2)
    for name in HP_FILES:
        with open(os.path.join(integ, name), "wb") as f:
            f.write(f"hp,{name}\r\n".encode())
    with open(os.path.join(integ, "geography_lookup.csv"), "wb") as f:
        f.write(b"LSOA21CD,LAEP\r\nE01,X\r\n")
    for name in ("dashboard_comparison_dno_monthly.csv", "dashboard_comparison_lsoa.csv", "dashboard_comparison_la.csv",
                 "dashboard_comparison_laep.csv"):
        with open(os.path.join(out, name), "wb") as f:
            f.write(b"period,value\r\n2026-03,1\r\n")
    for name in ("lsoa_ev_bodytype.csv", "unrelated_report.csv"):
        with open(os.path.join(out, name), "wb") as f:
            f.write(b"a,b\r\n1,2\r\n")
    return out


def make_candidate(root, run_id, forecast=None, with_ev_actuals=False, quarantined=False):
    run = os.path.join(root, "project", "dft_staging", f"run_{run_id}")
    os.makedirs(os.path.join(run, "outputs", "integration"), exist_ok=True)
    with open(os.path.join(run, "outputs", "lsoa_ev_bodytype.csv"), "wb") as f:
        f.write(b"period,LSOA21CD,ev_count\r\n")
    with open(os.path.join(run, "metadata.json"), "w") as f:
        json.dump({"run_id": run_id, "old_vintage": "2026-03-31", "new_vintage": "2026-03-31"}, f)
    if forecast is not None:
        write_csv(forecast, os.path.join(run, "outputs", "integration", "monthly_forecast_dno.csv"))
    if with_ev_actuals:
        with open(os.path.join(run, "outputs", "integration", "ev_actuals_dno.csv"), "wb") as f:
            f.write(b"period,EV_Type,value\r\n2011-12,BEV,314\r\n")
    if quarantined:
        with open(os.path.join(run, "QUARANTINED.json"), "w") as f:
            json.dump({"run_id": run_id, "reason": "test"}, f)
        with open(os.path.join(run, "qa_report.md"), "w") as f:
            f.write("QA STATUS: FAIL - QUARANTINED\n")
    return run


def run_python(code, cwd):
    """Run code in a fresh interpreter (for hard-interruption tests); never writes bytecode."""
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {PROJECT_ROOT!r})\n{code}"],
                          cwd=cwd, capture_output=True, text=True, env=env, timeout=300)
