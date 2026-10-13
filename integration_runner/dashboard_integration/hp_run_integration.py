#!/usr/bin/env python3
"""Build dated NESO/DFES HP anchors, monthly geography forecasts, and QA artefacts.

Parallel to the EV integration. Does not modify validated HP or EV production.
Does not overwrite live dashboard_comparison_*.csv or EV monthly_forecast_*.csv.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from .aggregate import interpolate_and_aggregate
from .geography import build_geography_lookup
from .hp_actuals import load_hp_actuals
from .hp_anchors import build_hp_annual_anchors
from .hp_config import (
    DASHBOARD_INTEGRATION_PUBLISH,
    EXCLUDED_DFES_LSOAS,
    HP_DISPLAY_END_PERIOD,
    HP_DISPLAY_START_PERIOD,
    HP_FINAL,
    HP_OUTPUT_DIR,
    HP_PUBLISH_FILES,
    HP_QA_DIR,
    NESO_HP_FILES,
    OLD_DFES_HP_MONTHLY,
    TECH_TYPE_HP,
)
from .hp_qa import (
    neso_hp_file_totals,
    protected_file_hashes,
    qa_accounting_108,
    qa_anchor_dates,
    qa_geography_reconciliation,
    qa_hp_actuals_unaltered,
    qa_interp_on_line,
    qa_interpolation_line,
    qa_lsoa_universe,
    qa_month_calendar,
    qa_monthly_anchor_lsoa,
    qa_monthly_anchor_totals,
    qa_neso_anchors_unchanged,
    qa_no_rounding,
    qa_no_total_bb_created,
    qa_source_distinction,
    qa_special_groups,
    qa_year_offset_zero,
)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _standardise_monthly(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["period_end"] = pd.to_datetime(df["period_end"]).dt.date
    df["anchor_date"] = pd.to_datetime(df["anchor_date"]).dt.date
    df["observation_source"] = df["forecast_source"]
    df = df.rename(columns={"forecast_value": "value"})
    return df


def _file_stats(path: Path) -> dict:
    if not path.exists():
        return {"path": str(path), "exists": False}
    return {
        "path": str(path),
        "exists": True,
        "rows": sum(1 for _ in path.open("r", encoding="utf-8", errors="replace")) - 1,
        "bytes": path.stat().st_size,
        "mb": round(path.stat().st_size / (1024 * 1024), 3),
    }


def _write_dfes_old_vs_new(monthly_dno: pd.DataFrame, qa_dir: Path) -> pd.DataFrame:
    if not OLD_DFES_HP_MONTHLY.exists():
        return pd.DataFrame()
    old = pd.read_csv(OLD_DFES_HP_MONTHLY)
    old_dno = old.groupby(["period", "DNO"], as_index=False)["forecast_value"].sum()
    old_dno = old_dno.rename(columns={"forecast_value": "old_value"})

    new = monthly_dno[
        (monthly_dno["forecast_source"] == "DFES")
        & (monthly_dno["period"].between("2025-04", "2026-03"))
    ].groupby(["period", "DNO"], as_index=False)["forecast_value"].sum()
    new = new.rename(columns={"forecast_value": "new_value"})

    cmp = old_dno.merge(new, on=["period", "DNO"], how="outer")
    cmp["abs_diff"] = (cmp["new_value"] - cmp["old_value"]).abs()
    cmp.to_csv(qa_dir / "hp_dfes_old_vs_new_interpolation.csv", index=False)

    summary = cmp.groupby("period", as_index=False).agg(
        old_ukpn=("old_value", "sum"),
        new_ukpn=("new_value", "sum"),
        max_abs_dno=("abs_diff", "max"),
    )
    summary["abs_diff"] = (summary["new_ukpn"] - summary["old_ukpn"]).abs()
    summary.to_csv(qa_dir / "hp_dfes_old_vs_new_summary.csv", index=False)
    return summary


def run() -> dict:
    HP_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    HP_QA_DIR.mkdir(parents=True, exist_ok=True)

    started = datetime.now()
    hashes_before = protected_file_hashes(_hash_file)
    file_totals = neso_hp_file_totals()

    print("Building geography lookup...", flush=True)
    geography = build_geography_lookup()
    geography.to_csv(HP_OUTPUT_DIR / "geography_lookup.csv", index=False)

    print("Building annual HP anchors (NESO + DFES 2024-2050)...", flush=True)
    anchors, neso_anchors, dfes_lineage, dfes_path = build_hp_annual_anchors()
    anchors.to_csv(HP_OUTPUT_DIR / "hp_annual_forecast_anchors.csv", index=False)
    dfes_lineage.to_csv(HP_QA_DIR / "hp_dfes_parameter_lineage.csv", index=False)

    print("Interpolating monthly HP stock and aggregating geography...", flush=True)
    monthly_dno, monthly_la, monthly_laep, coverage, neso_anchor_lsoa = interpolate_and_aggregate(
        anchors,
        geography,
        tech_type=TECH_TYPE_HP,
        subtype_col="technology_detail",
    )
    monthly_dno_std = _standardise_monthly(monthly_dno)
    monthly_la_std = _standardise_monthly(monthly_la)
    monthly_laep_std = _standardise_monthly(monthly_laep)
    monthly_dno_std.to_csv(HP_OUTPUT_DIR / "hp_monthly_forecast_dno.csv", index=False)
    monthly_la_std.to_csv(HP_OUTPUT_DIR / "hp_monthly_forecast_la.csv", index=False)
    monthly_laep_std.to_csv(HP_OUTPUT_DIR / "hp_monthly_forecast_laep.csv", index=False)
    coverage.to_csv(HP_QA_DIR / "hp_geography_coverage.csv", index=False)

    print("Loading existing HP actuals (unaltered)...", flush=True)
    actual_dno, actual_la, actual_laep, latest_actual, actual_notes = load_hp_actuals()
    actual_dno.to_csv(HP_OUTPUT_DIR / "hp_actuals_dno.csv", index=False)
    actual_la.to_csv(HP_OUTPUT_DIR / "hp_actuals_la.csv", index=False)
    actual_laep.to_csv(HP_OUTPUT_DIR / "hp_actuals_laep.csv", index=False)

    print("Running HP QA...", flush=True)
    monthly_for_qa = monthly_dno_std.rename(columns={"value": "forecast_value"})
    qa_frames = [
        qa_neso_anchors_unchanged(neso_anchors),
        qa_accounting_108(neso_anchors),
        qa_anchor_dates(anchors),
        qa_year_offset_zero(neso_anchors),
        qa_lsoa_universe(geography, neso_anchors),
        qa_monthly_anchor_lsoa(neso_anchor_lsoa),
        qa_monthly_anchor_totals(monthly_for_qa, neso_anchors),
        qa_geography_reconciliation(coverage),
        qa_source_distinction(anchors),
        qa_interpolation_line(),
        qa_interp_on_line(monthly_for_qa),
        qa_month_calendar(monthly_for_qa),
        qa_no_rounding(monthly_for_qa),
        qa_special_groups(neso_anchors),
        qa_hp_actuals_unaltered(actual_dno),
        qa_no_total_bb_created(anchors),
    ]
    qa_all = pd.concat(qa_frames, ignore_index=True)
    qa_all.to_csv(HP_QA_DIR / "hp_qa_summary.csv", index=False)
    neso_anchor_lsoa.to_csv(HP_QA_DIR / "hp_neso_lsoa_anchor_check.csv", index=False)

    dfes_cmp = _write_dfes_old_vs_new(monthly_for_qa, HP_QA_DIR)

    hashes_after = protected_file_hashes(_hash_file)
    protected_untouched = hashes_before == hashes_after
    qa_pass = bool(qa_all["pass"].all()) and protected_untouched

    file_stats = {
        name: _file_stats(HP_OUTPUT_DIR / name)
        for name in [
            "hp_annual_forecast_anchors.csv",
            "hp_monthly_forecast_dno.csv",
            "hp_monthly_forecast_la.csv",
            "hp_monthly_forecast_laep.csv",
            "hp_actuals_dno.csv",
            "hp_actuals_la.csv",
            "hp_actuals_laep.csv",
        ]
    }

    metadata = {
        "generated_at": datetime.now().isoformat(),
        "tech_type": TECH_TYPE_HP,
        "latest_actual_period": latest_actual,
        "actual_notes": actual_notes,
        "forecast_start_period": str(sorted(monthly_dno_std["period"].unique())[0]),
        "forecast_end_period": str(sorted(monthly_dno_std["period"].unique())[-1]),
        "data_horizon": "2025-03 to 2051-03",
        "display_start_period": HP_DISPLAY_START_PERIOD,
        "display_end_period": HP_DISPLAY_END_PERIOD,
        "display_horizon_note": (
            "Visible HP chart follows the existing dashboard window "
            f"{HP_DISPLAY_START_PERIOD} to {HP_DISPLAY_END_PERIOD}. "
            "Change display_end_period in dashboard.html (HP_DISPLAY_END_PERIOD) "
            "without deleting future forecast rows."
        ),
        "neso_horizon_years": "2024-2050",
        "dfes_horizon_years": "2024-2050",
        "neso_anchor_dates": "2025-03-31 to 2051-03-31",
        "year_offset": 0,
        "dfes_workbook": str(dfes_path),
        "hp_outputs_untouched": hashes_before.get(str(list(NESO_HP_FILES.values())[0]))
        == hashes_after.get(str(list(NESO_HP_FILES.values())[0])),
        "protected_files_untouched": protected_untouched,
        "qa_pass": qa_pass,
        "bb_mapping": {
            "Lct_BB005": "Domestic heat pump",
            "Lct_BB006": "Domestic hybrid heat pump",
            "Total Heat Pumps": "BB005 + BB006 at query/display only; no synthetic BB",
        },
        "file_stats": file_stats,
        "excluded_lsoas": sorted(EXCLUDED_DFES_LSOAS),
    }
    (HP_OUTPUT_DIR / "hp_metadata.json").write_text(json.dumps(metadata, indent=2, default=str), encoding="utf-8")

    report = _render_report(
        started=started,
        geography=geography,
        anchors=anchors,
        neso_anchors=neso_anchors,
        monthly_dno=monthly_dno_std,
        coverage=coverage,
        qa_all=qa_all,
        dfes_cmp=dfes_cmp,
        latest_actual=latest_actual,
        actual_notes=actual_notes,
        file_totals=file_totals,
        protected_untouched=protected_untouched,
        qa_pass=qa_pass,
        metadata=metadata,
        dfes_path=dfes_path,
        file_stats=file_stats,
    )
    (HP_OUTPUT_DIR / "hp_run_report.md").write_text(report, encoding="utf-8")
    (HP_QA_DIR / "hp_run_report.md").write_text(report, encoding="utf-8")

    if qa_pass:
        print("QA passed. Publishing HP copies to the dashboard integration folder...", flush=True)
        DASHBOARD_INTEGRATION_PUBLISH.mkdir(parents=True, exist_ok=True)
        for name in HP_PUBLISH_FILES:
            src = HP_OUTPUT_DIR / name
            if src.exists():
                (DASHBOARD_INTEGRATION_PUBLISH / name).write_bytes(src.read_bytes())
    else:
        print("QA FAILED. Dashboard HP files were not published.", flush=True)
        print(qa_all[~qa_all["pass"]].to_string(index=False), flush=True)

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
    actual_notes = ctx["actual_notes"]
    file_stats = ctx["file_stats"]

    combos = (
        anchors.groupby(["forecast_source", "forecast_scenario", "technology_detail", "building_block_id"])
        .size()
        .reset_index(name="n")
        .to_string(index=False)
    )
    ht_only = geography[geography["geography_flag"] == "HT_ONLY_BOUNDARY"]
    dash_only = geography[geography["geography_flag"] == "NESO_SPATIAL_NOT_ASSIGNED"]
    neso_cov = coverage[coverage["forecast_source"] == "NESO"]
    laep_share = (
        neso_cov["laep_total"].sum() / neso_cov["lsoa_total"].sum() if not neso_cov.empty and neso_cov["lsoa_total"].sum() else 0.0
    )

    stats_lines = []
    for name, info in file_stats.items():
        stats_lines.append(
            f"- {name}: {info.get('rows', 0):,} rows, {info.get('mb', 0)} MB"
        )

    dfes_cmp_txt = dfes_cmp.to_string(index=False) if isinstance(dfes_cmp, pd.DataFrame) and not dfes_cmp.empty else "Old DFES monthly file not compared or empty."

    lines = [
        "# HP monthly dashboard integration report",
        "",
        f"Generated: {metadata['generated_at']}",
        f"Runtime start: {ctx['started'].isoformat()}",
        f"HP_MONTHLY_DASHBOARD_INTEGRATION: {status}",
        "",
        "## Reuse vs HP-specific",
        "Reused: interpolate.py elapsed-day interpolation; geography.py lookup;",
        "aggregate.py monthly matrix + LA/LAEP/DNO roll-up (tech_type/subtype_col parameters);",
        "qa_interpolation_line; FY-end dating Year Y -> 31 Mar Y+1; year_offset=0;",
        "parallel outputs then publish-after-QA; no LSOA-month browser CSV.",
        "HP-specific: NESO BB005/BB006 readers; DFES domestic heating Parameter map;",
        "HP actuals copied from existing Stage 2F comparison files; special-group QA;",
        "108/108 C=D=E accounting; Exclusion A (12 LSOAs); hp_* output filenames;",
        "HP display-horizon constants separate from EV_DEMO_DISPLAY_END_PERIOD.",
        "",
        "## 1. Files created",
        "- dashboard_integration/outputs/hp_annual_forecast_anchors.csv",
        "- dashboard_integration/outputs/hp_monthly_forecast_dno.csv",
        "- dashboard_integration/outputs/hp_monthly_forecast_la.csv",
        "- dashboard_integration/outputs/hp_monthly_forecast_laep.csv",
        "- dashboard_integration/outputs/hp_actuals_*.csv",
        "- dashboard_integration/outputs/hp_metadata.json",
        "- dashboard_integration/outputs/hp_run_report.md",
        "- dashboard_integration/integration_qa/hp_*",
        "",
        "## 2. Files modified",
        "aggregate.py: optional tech_type and subtype_col (EV defaults unchanged).",
        "Validated HP/EV production, tRESP scripts, and dashboard_comparison_*.csv not modified.",
        "dashboard.html is updated only after QA PASS, in a separate step.",
        "",
        "## 3. Annual HP anchor row counts",
        f"- All anchors: {len(anchors):,}",
        f"- NESO: {len(neso_anchors):,}",
        f"- DFES: {len(anchors[anchors.forecast_source=='DFES']):,}",
        f"- Unique NESO LSOAs: {neso_anchors['LSOA21CD'].nunique():,}",
        "",
        "## 4. Monthly HP forecast period range",
        f"- Data: {metadata['forecast_start_period']} to {metadata['forecast_end_period']}",
        f"- Display (existing HP window): {HP_DISPLAY_START_PERIOD} to {HP_DISPLAY_END_PERIOD}",
        "",
        "## 5. BB005/BB006 mapping",
        "- Lct_BB005 -> Domestic heat pump",
        "- Lct_BB006 -> Domestic hybrid heat pump",
        "- Total Heat Pumps = BB005 + BB006 at query/display only. No synthetic BB.",
        "- Existing dashboard Heat Pump chart was a single combined measure (actuals from",
        "  MCS/LCT/ECR etc.; DFES Domestic+I&C via 07_dfes_heat_pump_processing.py).",
        "- New NESO traces are domestic BB005+BB006. New DFES traces use the same two",
        "  domestic Parameters so NESO vs DFES is like-for-like. I&C is not a NESO BB",
        "  here and is omitted from the new DFES series. Actual HP values are unchanged.",
        f"- DFES workbook: {ctx['dfes_path']}",
        "",
        "## 6. Source/scenario combinations",
        combos,
        "",
        "## 7. Latest defensible HP actual period",
        f"- {ctx['latest_actual']}",
        f"- Source: {actual_notes.get('source')}",
        f"- Grain: {actual_notes.get('grain')}",
        f"- Window: {actual_notes.get('window')}",
        f"- Rule: {actual_notes.get('window_rule')}",
        "",
        "## 8-16. QA",
        qa_all.to_string(index=False),
        "",
        "## 17. 12 excluded LSOAs",
        f"- Codes: {', '.join(sorted(EXCLUDED_DFES_LSOAS))}",
        "- geography_flag=NESO_SPATIAL_NOT_ASSIGNED; no NESO HP forecast rows created.",
        "- DFES weights not redistributed. Actuals may still include them.",
        "",
        "## 66 HT-only LSOAs",
        f"- Count: {ht_only['LSOA21CD'].nunique()}",
        f"- NESO volume: {neso_anchors[neso_anchors.LSOA21CD.isin(ht_only.LSOA21CD)]['forecast_value'].sum()}",
        "",
        "## LAEP coverage",
        f"- NESO volume share in LAEP-mapped LSOAs: {laep_share:.4f}",
        "- LA/DNO retain full NESO volume. LAEP is a documented incomplete subset.",
        "",
        "## DFES old vs new interpolation",
        dfes_cmp_txt,
        "",
        "Old method: month_idx/11, integer rounding, Domestic+I&C, FY 2025/26 only.",
        "New method: elapsed-day interpolation, domestic BB005/BB006 only, 2024-2050.",
        "March anchors and intervening months will differ by design and by I&C scope.",
        "",
        "## Browser-facing file sizes",
        *stats_lines,
        "",
        "## Protected files",
        f"- Validated HP and EV production plus dashboard_comparison hashes unchanged: {ctx['protected_untouched']}",
        f"- NESO file totals: {ctx['file_totals']}",
        "",
        "## Remaining REVIEW items",
        "- Validated HP production remains REVIEW for 6 distribution-QA observations.",
        "- Those flags are not cleared by dashboard integration.",
        f"- LAEP incomplete (NESO share {laep_share:.4f}).",
        "",
        "## Failed checks" if not failed.empty else "## Failed checks",
        failed.to_string(index=False) if not failed.empty else "None",
        "",
        f"HP_MONTHLY_DASHBOARD_INTEGRATION: {status}",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    run()
