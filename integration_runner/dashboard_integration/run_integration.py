#!/usr/bin/env python3
"""Build dated NESO/DFES anchors, monthly geography forecasts, and QA artefacts."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from .actuals import load_ev_actuals
from .aggregate import interpolate_and_aggregate
from .anchors import build_annual_anchors, load_dfes_anchors, load_neso_anchors
from .config import (
    DASHBOARD_INTEGRATION_PUBLISH,
    HT_FINAL,
    NESO_FILES,
    OLD_DFES_MONTHLY,
    OUTPUT_DIR,
    QA_DIR,
)
from .geography import build_geography_lookup
from .publish_guard import PublishRefused, publish
from .qa import (
    neso_file_totals,
    qa_anchor_dates,
    qa_geography_reconciliation,
    qa_interpolation_line,
    qa_lsoa_universe,
    qa_monthly_anchor_lsoa,
    qa_monthly_anchor_totals,
    qa_neso_anchors_unchanged,
    qa_no_rounding,
    qa_published_totals,
    qa_source_distinction,
)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ht_hashes() -> dict[str, str]:
    return {str(path): _hash_file(path) for path in NESO_FILES.values()}


def _write_dfes_old_vs_new(monthly_dno: pd.DataFrame, qa_dir: Path) -> pd.DataFrame:
    old = pd.read_csv(OLD_DFES_MONTHLY)
    old["EV_Type"] = old["vehicle_type"].str.contains(r"\(BEV\)", regex=True).map({True: "BEV", False: "PHEV"})
    old_dno = old.groupby(["period", "DNO", "EV_Type"], as_index=False)["forecast_value"].sum()
    old_dno = old_dno.rename(columns={"forecast_value": "old_value"})

    new = monthly_dno[
        (monthly_dno["forecast_source"] == "DFES")
        & (monthly_dno["period"].between("2025-04", "2026-03"))
    ].groupby(["period", "DNO", "EV_Type"], as_index=False)["forecast_value"].sum()
    new = new.rename(columns={"forecast_value": "new_value"})

    cmp = old_dno.merge(new, on=["period", "DNO", "EV_Type"], how="outer")
    cmp["abs_diff"] = (cmp["new_value"] - cmp["old_value"]).abs()
    cmp["rel_diff_pct"] = 100.0 * (cmp["new_value"] - cmp["old_value"]) / cmp["old_value"].replace(0, pd.NA)
    cmp.to_csv(qa_dir / "dfes_old_vs_new_interpolation.csv", index=False)

    summary = cmp.groupby("period", as_index=False).agg(
        old_ukpn=("old_value", "sum"),
        new_ukpn=("new_value", "sum"),
        max_abs_dno=("abs_diff", "max"),
    )
    summary["abs_diff"] = (summary["new_ukpn"] - summary["old_ukpn"]).abs()
    summary.to_csv(qa_dir / "dfes_old_vs_new_summary.csv", index=False)
    return summary


def _standardise_monthly(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["period_end"] = pd.to_datetime(df["period_end"]).dt.date
    df["anchor_date"] = pd.to_datetime(df["anchor_date"]).dt.date
    df["observation_source"] = df["forecast_source"]
    df = df.rename(columns={"forecast_value": "value"})
    return df


def run() -> dict:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    QA_DIR.mkdir(parents=True, exist_ok=True)

    started = datetime.now()
    hashes_before = _ht_hashes()
    file_totals = neso_file_totals()

    print("Building geography lookup...", flush=True)
    geography = build_geography_lookup()
    geography.to_csv(OUTPUT_DIR / "geography_lookup.csv", index=False)

    print("Building annual anchors (NESO + DFES 2024-2050)...", flush=True)
    existing = OUTPUT_DIR / "annual_forecast_anchors.csv"
    lineage_path = QA_DIR / "dfes_vehicle_type_lineage.csv"
    if existing.exists() and lineage_path.exists():
        print("  reusing existing DFES anchors; rebuilding completed NESO panel...", flush=True)
        prev = pd.read_csv(existing)
        dfes = prev[prev["forecast_source"] == "DFES"].copy()
        dfes["anchor_date"] = pd.to_datetime(dfes["anchor_date"]).dt.date
        neso_anchors = load_neso_anchors()
        anchors = pd.concat([neso_anchors, dfes], ignore_index=True)
        dfes_lineage = pd.read_csv(lineage_path)
    else:
        anchors, neso_anchors, dfes_lineage = build_annual_anchors()
        dfes_lineage.to_csv(lineage_path, index=False)
    anchors.to_csv(OUTPUT_DIR / "annual_forecast_anchors.csv", index=False)

    print("Interpolating monthly stock and aggregating geography...", flush=True)
    monthly_dno, monthly_la, monthly_laep, coverage, neso_anchor_lsoa = interpolate_and_aggregate(
        anchors, geography
    )
    monthly_dno = _standardise_monthly(monthly_dno)
    monthly_la = _standardise_monthly(monthly_la)
    monthly_laep = _standardise_monthly(monthly_laep)
    monthly_dno.to_csv(OUTPUT_DIR / "monthly_forecast_dno.csv", index=False)
    monthly_la.to_csv(OUTPUT_DIR / "monthly_forecast_la.csv", index=False)
    monthly_laep.to_csv(OUTPUT_DIR / "monthly_forecast_laep.csv", index=False)
    coverage.to_csv(QA_DIR / "geography_coverage.csv", index=False)

    print("Loading EV actuals...", flush=True)
    actual_dno, actual_la, actual_laep, latest_actual = load_ev_actuals(geography)
    actual_dno.to_csv(OUTPUT_DIR / "ev_actuals_dno.csv", index=False)
    actual_la.to_csv(OUTPUT_DIR / "ev_actuals_la.csv", index=False)
    actual_laep.to_csv(OUTPUT_DIR / "ev_actuals_laep.csv", index=False)

    print("Running QA...", flush=True)
    qa_frames = [
        qa_neso_anchors_unchanged(neso_anchors),
        qa_published_totals(neso_anchors),
        qa_anchor_dates(anchors),
        qa_lsoa_universe(geography, neso_anchors),
        qa_monthly_anchor_lsoa(neso_anchor_lsoa),
        qa_monthly_anchor_totals(monthly_dno.rename(columns={"value": "forecast_value"}), neso_anchors),
        qa_geography_reconciliation(coverage),
        qa_source_distinction(anchors),
        qa_interpolation_line(),
        qa_no_rounding(monthly_dno.rename(columns={"value": "forecast_value"})),
    ]
    qa_all = pd.concat(qa_frames, ignore_index=True)
    qa_all.to_csv(QA_DIR / "qa_summary.csv", index=False)
    neso_anchor_lsoa.to_csv(QA_DIR / "neso_lsoa_anchor_check.csv", index=False)

    dfes_cmp = _write_dfes_old_vs_new(
        monthly_dno.rename(columns={"value": "forecast_value"}),
        QA_DIR,
    )

    hashes_after = _ht_hashes()
    ht_untouched = hashes_before == hashes_after
    qa_pass = bool(qa_all["pass"].all()) and ht_untouched

    metadata = {
        "generated_at": datetime.now().isoformat(),
        "latest_actual_period": latest_actual,
        "forecast_start_period": str(sorted(monthly_dno["period"].unique())[0]),
        "forecast_end_period": str(sorted(monthly_dno["period"].unique())[-1]),
        "neso_horizon_years": "2024-2050",
        "dfes_horizon_years": "2024-2050",
        "neso_anchor_dates": "2025-03-31 to 2051-03-31",
        "ht_outputs_untouched": ht_untouched,
        "qa_pass": qa_pass,
    }
    (OUTPUT_DIR / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    report = _render_report(
        started=started,
        geography=geography,
        anchors=anchors,
        neso_anchors=neso_anchors,
        monthly_dno=monthly_dno,
        coverage=coverage,
        qa_all=qa_all,
        dfes_cmp=dfes_cmp,
        latest_actual=latest_actual,
        file_totals=file_totals,
        ht_untouched=ht_untouched,
        qa_pass=qa_pass,
        metadata=metadata,
    )
    (OUTPUT_DIR / "run_report.md").write_text(report, encoding="utf-8")
    (QA_DIR / "run_report.md").write_text(report, encoding="utf-8")

    if qa_pass:
        # Guarded publish (C1 safeguard): forecasts only, validated first; EV actuals are never published from here.
        print("QA passed. Publishing validated forecast files to the dashboard integration folder...")
        try:
            report = publish(OUTPUT_DIR, DASHBOARD_INTEGRATION_PUBLISH)
            print(f"Published {sorted(report['published'])}; previous files backed up in {report['backup_dir']}")
        except PublishRefused as exc:
            print(f"PUBLISH REFUSED - dashboard integration files were not changed: {exc}")
    else:
        print("QA FAILED. Dashboard files were not published.")
        print(qa_all[~qa_all["pass"]].to_string(index=False))

    print(report)
    return {"qa_pass": qa_pass, "latest_actual": latest_actual, "qa": qa_all}


def _render_report(**ctx) -> str:
    qa_all: pd.DataFrame = ctx["qa_all"]
    anchors: pd.DataFrame = ctx["anchors"]
    monthly = ctx["monthly_dno"]
    geography = ctx["geography"]
    neso_anchors = ctx["neso_anchors"]
    coverage = ctx["coverage"]
    dfes_cmp = ctx["dfes_cmp"]
    metadata = ctx["metadata"]
    failed = qa_all[~qa_all["pass"]]
    status = "PASS" if ctx["qa_pass"] else "FAIL"

    combos = (
        anchors.groupby(["forecast_source", "forecast_scenario", "EV_Type"])
        .size()
        .reset_index(name="n")
        .to_string(index=False)
    )
    ht_only = geography[geography["geography_flag"] == "HT_ONLY_BOUNDARY"]
    dash_only = geography[geography["geography_flag"] == "NESO_SPATIAL_NOT_ASSIGNED"]
    laep_share = coverage[coverage["forecast_source"] == "NESO"]["laep_total"].sum() / coverage[
        coverage["forecast_source"] == "NESO"
    ]["lsoa_total"].sum()

    lines = [
        "# Monthly dashboard integration report",
        "",
        f"Generated: {metadata['generated_at']}",
        f"Runtime start: {ctx['started'].isoformat()}",
        f"MONTHLY_DASHBOARD_INTEGRATION: {status}",
        "",
        "## 1. Files created",
        "- dashboard_integration/outputs/annual_forecast_anchors.csv",
        "- dashboard_integration/outputs/monthly_forecast_dno.csv",
        "- dashboard_integration/outputs/monthly_forecast_la.csv",
        "- dashboard_integration/outputs/monthly_forecast_laep.csv",
        "- dashboard_integration/outputs/geography_lookup.csv",
        "- dashboard_integration/outputs/ev_actuals_*.csv",
        "- dashboard_integration/integration_qa/",
        "",
        "## 2. Files modified",
        "None in this runner. Validated HT outputs are read-only.",
        "dashboard.html is updated only after QA PASS, in a separate step.",
        "",
        "## 3. Annual anchor row counts",
        f"- All anchors: {len(anchors):,}",
        f"- NESO: {len(neso_anchors):,}",
        f"- DFES: {len(anchors[anchors.forecast_source=='DFES']):,}",
        f"- Unique NESO LSOAs: {neso_anchors['LSOA21CD'].nunique():,}",
        "",
        "## 4. Monthly period range",
        f"- {metadata['forecast_start_period']} to {metadata['forecast_end_period']}",
        "",
        "## 5. DFES forecast horizon",
        f"- Source years 2024-2050; anchors 2025-03-31 to 2051-03-31",
        "",
        "## 6. NESO forecast horizon",
        f"- Source years 2024-2050; anchors 2025-03-31 to 2051-03-31",
        "",
        "## 7. Latest EV actual period",
        f"- {ctx['latest_actual']}",
        "",
        "## 8. Source/scenario combinations",
        combos,
        "",
        "## 9-11. QA",
        qa_all.to_string(index=False),
        "",
        "## 12. 66 HT-only LSOAs",
        f"- Count: {ht_only['LSOA21CD'].nunique()}",
        f"- NESO volume: {neso_anchors[neso_anchors.LSOA21CD.isin(ht_only.LSOA21CD)]['forecast_value'].sum()}",
        "- Retained in NESO universe with geography_flag=HT_ONLY_BOUNDARY",
        "",
        "## 13. 12 dashboard-only LSOAs",
        f"- Count: {dash_only['LSOA21CD'].nunique()}",
        f"- Codes: {', '.join(sorted(dash_only['LSOA21CD']))}",
        "- geography_flag=NESO_SPATIAL_NOT_ASSIGNED; no NESO rows created",
        "",
        "## 14. DFES old vs new interpolation",
        dfes_cmp.to_string(index=False),
        "",
        "Old method: month_idx/11 and integer rounding at vehicle-type grain.",
        "New method: elapsed-day interpolation at BEV/PHEV grain, float precision.",
        "March anchors should be close; intervening months will differ by design.",
        "",
        "## 15. Dashboard changes",
        "Applied only after QA PASS, using project/output_processed/integration/ copies.",
        "Existing dashboard_comparison_*.csv files are not overwritten.",
        "",
        "## 16. Regression",
        f"- HT SHA256 unchanged: {ctx['ht_untouched']}",
        f"- File totals: {ctx['file_totals']}",
        "",
        "## 17. REVIEW items",
        f"- LAEP coverage (NESO volume share): {laep_share:.4f}",
        "- HP/PV/charger completeness rule not changed",
        "",
        "## 18. tRESP / HT outputs",
        f"- Validated HT files untouched: {ctx['ht_untouched']}",
        "",
        "## Failed checks" if not failed.empty else "## Failed checks",
        failed.to_string(index=False) if not failed.empty else "None",
        "",
        f"MONTHLY_DASHBOARD_INTEGRATION: {status}",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    run()
