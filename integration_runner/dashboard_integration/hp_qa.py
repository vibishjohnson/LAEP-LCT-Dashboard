"""Mandatory NESO HP, geography, interpolation, special-group, and regression QA."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from .config import HT_FINAL
from .hp_config import (
    DASHBOARD_COMPARISON_DNO,
    DASHBOARD_COMPARISON_LA,
    DASHBOARD_COMPARISON_LAEP,
    EXCLUDED_DFES_LSOAS,
    FLOAT_TOL,
    GEO_TOL,
    HP_ACCOUNTING,
    HP_FINAL,
    HT_ONLY_COUNT,
    NESO_HP_FILES,
    NESO_LSOA_COUNT,
    NEW_CROSS_SPATIAL,
    SPECIAL_SPATIAL_GROUPS,
)
from .interpolate import interpolate_stock, month_ends_inclusive
from .qa import qa_interpolation_line


def neso_hp_file_totals() -> dict[tuple[str, str], float]:
    totals = {}
    for (scenario, bb, _detail), path in NESO_HP_FILES.items():
        raw = pd.read_csv(path, usecols=["forecast_value"])
        totals[(scenario, bb)] = float(raw["forecast_value"].sum())
    return totals


def qa_neso_anchors_unchanged(neso_anchors: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (scenario, bb, _detail), path in NESO_HP_FILES.items():
        raw = pd.read_csv(path, usecols=["year", "LSOA21CD", "forecast_value"])
        got = neso_anchors[
            (neso_anchors["forecast_scenario"] == scenario)
            & (neso_anchors["building_block_id"] == bb)
        ][["anchor_year", "LSOA21CD", "forecast_value"]].rename(columns={"anchor_year": "year"})
        merged = raw.merge(
            got, on=["year", "LSOA21CD"], suffixes=("_file", "_anchor"), how="outer", indicator=True
        )
        matched = merged[merged["_merge"] == "both"]
        extra = merged[merged["_merge"] == "right_only"]
        missing = merged[merged["_merge"] == "left_only"]
        max_abs = (matched["forecast_value_file"] - matched["forecast_value_anchor"]).abs().max()
        extra_ok = extra.empty or float(extra["forecast_value_anchor"].fillna(1).abs().max()) <= FLOAT_TOL
        rows.append(
            {
                "check": "NESO_ANNUAL_EXACT",
                "forecast_scenario": scenario,
                "building_block_id": bb,
                "n_file": len(raw),
                "n_anchor": len(got),
                "n_matched": len(matched),
                "max_abs_diff": float(max_abs) if pd.notna(max_abs) else None,
                "pass": len(missing) == 0 and extra_ok and (0 if pd.isna(max_abs) else max_abs) <= FLOAT_TOL,
            }
        )
    return pd.DataFrame(rows)


def qa_accounting_108(neso_anchors: pd.DataFrame) -> pd.DataFrame:
    accounting = pd.read_csv(HP_ACCOUNTING)
    got = (
        neso_anchors.groupby(["forecast_scenario", "building_block_id", "anchor_year"], as_index=False)[
            "forecast_value"
        ]
        .sum()
        .rename(columns={"forecast_scenario": "scenario", "building_block_id": "bb", "anchor_year": "year"})
    )
    merged = accounting.merge(got, on=["scenario", "bb", "year"], how="outer", suffixes=("_acct", "_anchor"))
    merged["abs_c_e"] = (merged["C_A_minus_B"] - merged["E_LSOA"]).abs()
    merged["abs_anchor_e"] = (merged["forecast_value"] - merged["E_LSOA"]).abs()
    n_cells = len(accounting)
    n_pass_acct = int(accounting["pass"].sum()) if "pass" in accounting.columns else 0
    n_bad_anchor = int((merged["abs_anchor_e"].fillna(1) > FLOAT_TOL).sum())
    n_bad_ce = int((merged["abs_c_e"].fillna(1) > FLOAT_TOL).sum())
    return pd.DataFrame(
        [
            {
                "check": "ACCOUNTING_108_C_EQ_D_EQ_E",
                "n_cells": n_cells,
                "n_pass_file": n_pass_acct,
                "n_bad_anchor_vs_E": n_bad_anchor,
                "n_bad_C_vs_E": n_bad_ce,
                "max_abs_anchor_vs_E": float(merged["abs_anchor_e"].max()) if merged["abs_anchor_e"].notna().any() else None,
                "pass": n_cells == 108 and n_pass_acct == 108 and n_bad_anchor == 0 and n_bad_ce == 0,
            }
        ]
    )


def qa_anchor_dates(anchors: pd.DataFrame) -> pd.DataFrame:
    years = anchors["anchor_year"].astype(int)
    expected = years.map(lambda y: date(y + 1, 3, 31))
    got = pd.to_datetime(anchors["anchor_date"]).dt.date
    ok = (got == expected).all()
    return pd.DataFrame(
        [
            {
                "check": "ANCHOR_DATE_31_MAR_Y_PLUS_1",
                "n_rows": len(anchors),
                "n_bad": int((got != expected).sum()),
                "pass": bool(ok),
            }
        ]
    )


def qa_lsoa_universe(geography: pd.DataFrame, neso_anchors: pd.DataFrame) -> pd.DataFrame:
    neso_geo = geography[geography["in_neso_spatial"]]
    ht_only = geography[geography["geography_flag"] == "HT_ONLY_BOUNDARY"]
    dash_only = geography[geography["geography_flag"] == "NESO_SPATIAL_NOT_ASSIGNED"]
    neso_lsoas = set(neso_anchors["LSOA21CD"])
    excluded_in_neso = neso_anchors[neso_anchors["LSOA21CD"].isin(EXCLUDED_DFES_LSOAS)]
    excluded_positive = excluded_in_neso[excluded_in_neso["forecast_value"].abs() > FLOAT_TOL]
    rows = [
        {
            "check": "NESO_LSOA_COUNT",
            "expected": NESO_LSOA_COUNT,
            "actual": int(neso_geo["LSOA21CD"].nunique()),
            "pass": neso_geo["LSOA21CD"].nunique() == NESO_LSOA_COUNT
            and neso_anchors["LSOA21CD"].nunique() == NESO_LSOA_COUNT,
        },
        {
            "check": "HT_ONLY_RETAINED",
            "expected": HT_ONLY_COUNT,
            "actual": int(ht_only["LSOA21CD"].nunique()),
            "pass": ht_only["LSOA21CD"].nunique() == HT_ONLY_COUNT
            and set(ht_only["LSOA21CD"]).issubset(neso_lsoas),
        },
        {
            "check": "DASHBOARD_ONLY_NO_NESO",
            "expected": 0,
            "actual": int(neso_anchors[neso_anchors["LSOA21CD"].isin(dash_only["LSOA21CD"])].shape[0]),
            "pass": not neso_anchors["LSOA21CD"].isin(dash_only["LSOA21CD"]).any(),
        },
        {
            "check": "EXCLUDED_12_NOT_IN_NESO",
            "expected": 0,
            "actual": int(excluded_in_neso["LSOA21CD"].nunique()) if not excluded_in_neso.empty else 0,
            "n_positive": int(excluded_positive.shape[0]),
            "pass": excluded_in_neso.empty,
        },
        {
            "check": "EXCLUDED_12_SET",
            "expected": 12,
            "actual": len(EXCLUDED_DFES_LSOAS),
            "pass": len(EXCLUDED_DFES_LSOAS) == 12
            and set(dash_only["LSOA21CD"]) == set(EXCLUDED_DFES_LSOAS),
        },
    ]
    return pd.DataFrame(rows)


def qa_monthly_anchor_lsoa(neso_anchor_df: pd.DataFrame) -> pd.DataFrame:
    if neso_anchor_df.empty:
        return pd.DataFrame([{"check": "NESO_MONTHLY_LSOA_ANCHOR", "pass": False, "detail": "empty"}])
    max_abs = float(neso_anchor_df["abs_diff"].max())
    n_bad = int((neso_anchor_df["abs_diff"] > FLOAT_TOL).sum())
    n_null = int(neso_anchor_df["monthly_value"].isna().sum() + neso_anchor_df["forecast_value"].isna().sum())
    return pd.DataFrame(
        [
            {
                "check": "NESO_MONTHLY_LSOA_ANCHOR",
                "n_rows": len(neso_anchor_df),
                "n_bad": n_bad,
                "n_null": n_null,
                "max_abs_diff": max_abs,
                "pass": n_bad == 0 and n_null == 0 and max_abs <= FLOAT_TOL,
            }
        ]
    )


def qa_monthly_anchor_totals(monthly_dno: pd.DataFrame, neso_anchors: pd.DataFrame) -> pd.DataFrame:
    rows = []
    neso_m = monthly_dno[(monthly_dno["forecast_source"] == "NESO") & (monthly_dno["is_anchor"])]
    annual = neso_anchors.groupby(
        ["forecast_scenario", "building_block_id", "anchor_year", "technology_detail"],
        as_index=False,
    )["forecast_value"].sum()
    annual["period"] = annual["anchor_year"].map(lambda y: f"{int(y) + 1}-03")
    monthly = neso_m.groupby(
        ["forecast_scenario", "building_block_id", "period", "technology_detail"],
        as_index=False,
    )["forecast_value"].sum()
    merged = annual.merge(
        monthly,
        on=["forecast_scenario", "building_block_id", "period", "technology_detail"],
        suffixes=("_annual", "_monthly"),
        how="outer",
    )
    merged["abs_diff"] = (merged["forecast_value_annual"] - merged["forecast_value_monthly"]).abs()
    for (scenario, bb), part in merged.groupby(["forecast_scenario", "building_block_id"]):
        rows.append(
            {
                "check": "NESO_MONTHLY_ANCHOR_TOTAL",
                "forecast_scenario": scenario,
                "building_block_id": bb,
                "n_years": part["anchor_year"].nunique(),
                "max_abs_diff": float(part["abs_diff"].max()) if part["abs_diff"].notna().any() else None,
                "pass": bool(part["abs_diff"].fillna(1).max() <= FLOAT_TOL),
            }
        )
    return pd.DataFrame(rows)


def qa_geography_reconciliation(coverage: pd.DataFrame) -> pd.DataFrame:
    coverage = coverage.copy()
    coverage["lsoa_vs_la"] = (coverage["lsoa_total"] - coverage["la_total"]).abs()
    coverage["lsoa_vs_dno"] = (coverage["lsoa_total"] - coverage["dno_total"]).abs()
    coverage["laep_share"] = coverage["laep_total"] / coverage["lsoa_total"].replace(0, pd.NA)
    neso = coverage[coverage["forecast_source"] == "NESO"]
    all_src = coverage
    return pd.DataFrame(
        [
            {
                "check": "GEO_LSOA_EQ_LA",
                "max_abs_diff": float(all_src["lsoa_vs_la"].max()) if not all_src.empty else None,
                "pass": bool(all_src["lsoa_vs_la"].max() <= GEO_TOL) if not all_src.empty else False,
            },
            {
                "check": "GEO_LSOA_EQ_DNO",
                "max_abs_diff": float(all_src["lsoa_vs_dno"].max()) if not all_src.empty else None,
                "pass": bool(all_src["lsoa_vs_dno"].max() <= GEO_TOL) if not all_src.empty else False,
            },
            {
                "check": "GEO_LAEP_INCOMPLETE_DOCUMENTED",
                "min_laep_share": float(neso["laep_share"].min()) if neso["laep_share"].notna().any() else None,
                "max_laep_share": float(neso["laep_share"].max()) if neso["laep_share"].notna().any() else None,
                "pass": bool((neso["laep_total"] <= neso["lsoa_total"] + GEO_TOL).all()) if not neso.empty else False,
            },
        ]
    )


def qa_source_distinction(anchors: pd.DataFrame) -> pd.DataFrame:
    combos = (
        anchors.groupby(["forecast_source", "forecast_scenario", "technology_detail"])
        .size()
        .reset_index(name="n")
    )
    has_dfes_ht = (
        (combos["forecast_source"] == "DFES") & (combos["forecast_scenario"] == "HolisticTransition")
    ).any()
    has_neso_ht = (
        (combos["forecast_source"] == "NESO") & (combos["forecast_scenario"] == "HolisticTransition")
    ).any()
    has_neso_htb = (
        (combos["forecast_source"] == "NESO") & (combos["forecast_scenario"] == "HolisticTransitionBasic")
    ).any()
    has_bb005 = (combos["technology_detail"] == "Domestic heat pump").any()
    has_bb006 = (combos["technology_detail"] == "Domestic hybrid heat pump").any()
    return pd.DataFrame(
        [
            {
                "check": "SOURCE_SCENARIO_DISTINCT",
                "dfes_ht": bool(has_dfes_ht),
                "neso_ht": bool(has_neso_ht),
                "neso_htb": bool(has_neso_htb),
                "bb005": bool(has_bb005),
                "bb006": bool(has_bb006),
                "n_combos": len(combos),
                "pass": bool(has_dfes_ht and has_neso_ht and has_neso_htb and has_bb005 and has_bb006),
            }
        ]
    )


def qa_no_rounding(monthly: pd.DataFrame) -> pd.DataFrame:
    neso = monthly[monthly["forecast_source"] == "NESO"]
    frac = (~neso["forecast_value"].apply(lambda x: float(x).is_integer())).mean() if not neso.empty else 0
    return pd.DataFrame(
        [
            {
                "check": "FRACTIONAL_VALUES_PRESENT",
                "fraction_non_integer": float(frac),
                "pass": bool(frac > 0),
            }
        ]
    )


def qa_month_calendar(monthly_dno: pd.DataFrame) -> pd.DataFrame:
    neso = monthly_dno[monthly_dno["forecast_source"] == "NESO"].copy()
    neso["period_end"] = pd.to_datetime(neso["period_end"]).dt.date
    start = date(2025, 3, 31)
    end = date(2051, 3, 31)
    expected = month_ends_inclusive(start, end)
    rows = []
    for (scenario, bb), part in neso.groupby(["forecast_scenario", "building_block_id"]):
        periods = sorted(set(part["period_end"]))
        extra = [p for p in periods if p > end or p < start]
        missing = [p for p in expected if p not in set(periods)]
        dup = int(part.duplicated(["period", "DNO"]).sum())
        rows.append(
            {
                "check": "MONTH_CALENDAR",
                "forecast_scenario": scenario,
                "building_block_id": bb,
                "n_expected": len(expected),
                "n_got": len(periods),
                "n_missing": len(missing),
                "n_extra": len(extra),
                "n_dup": dup,
                "pass": periods == expected and dup == 0 and not extra,
            }
        )
    return pd.DataFrame(rows)


def qa_interp_on_line(monthly_dno: pd.DataFrame) -> pd.DataFrame:
    neso = monthly_dno[
        (monthly_dno["forecast_source"] == "NESO")
        & (monthly_dno["forecast_scenario"] == "HolisticTransition")
        & (monthly_dno["building_block_id"] == "Lct_BB005")
        & (monthly_dno["DNO"] == "EPN")
    ].copy()
    neso["period_end"] = pd.to_datetime(neso["period_end"]).dt.date
    neso = neso.sort_values("period_end")
    anchors = neso[neso["is_anchor"]].copy()
    if len(anchors) < 2:
        return pd.DataFrame([{"check": "INTERP_DNO_ON_LINE", "pass": False, "detail": "insufficient anchors"}])
    t0 = anchors.iloc[0]["period_end"]
    t1 = anchors.iloc[1]["period_end"]
    v0 = float(anchors.iloc[0]["forecast_value"])
    v1 = float(anchors.iloc[1]["forecast_value"])
    mid_rows = neso[(neso["period_end"] > t0) & (neso["period_end"] < t1)]
    max_abs = 0.0
    n_bad = 0
    for _, row in mid_rows.iterrows():
        expected = interpolate_stock(row["period_end"], t0, v0, t1, v1)
        diff = abs(float(row["forecast_value"]) - expected)
        max_abs = max(max_abs, diff)
        if diff > FLOAT_TOL:
            n_bad += 1
    leap_ok = True
    leap_span = neso[(neso["period_end"] >= date(2024, 3, 31)) & (neso["period_end"] <= date(2025, 3, 31))]
    if not leap_span.empty:
        leap_ok = True
    return pd.DataFrame(
        [
            {
                "check": "INTERP_DNO_ON_LINE",
                "n_mid_months": len(mid_rows),
                "n_bad": n_bad,
                "max_abs_diff": max_abs,
                "pass": n_bad == 0 and len(mid_rows) > 0,
            },
            {
                "check": "INTERP_NO_EXTRAPOLATION",
                "max_period": str(neso["period_end"].max()),
                "pass": neso["period_end"].max() == date(2051, 3, 31)
                and neso["period_end"].min() == date(2025, 3, 31),
            },
        ]
    )


def qa_special_groups(neso_anchors: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for (scenario, bb, _detail), path in NESO_HP_FILES.items():
        raw = pd.read_csv(path, usecols=["scenario", "bb", "year", "LSOA21CD", "spatial_gsp", "forecast_value"])
        raw["forecast_scenario"] = scenario
        raw["building_block_id"] = bb
        frames.append(raw)
    files = pd.concat(frames, ignore_index=True)
    rows = []
    groups = dict(SPECIAL_SPATIAL_GROUPS)
    groups["New Cross / Wimbledon / Bengeworth"] = NEW_CROSS_SPATIAL
    for name, spatial in groups.items():
        file_part = files[files["spatial_gsp"].isin(spatial)]
        file_tot = file_part.groupby(["forecast_scenario", "building_block_id", "year"], as_index=False)[
            "forecast_value"
        ].sum()
        lsoas = set(file_part["LSOA21CD"])
        if not lsoas:
            rows.append({"check": "SPECIAL_GROUP", "group": name, "pass": False, "detail": "no LSOAs"})
            continue
        if lsoas & EXCLUDED_DFES_LSOAS:
            rows.append(
                {
                    "check": "SPECIAL_GROUP",
                    "group": name,
                    "pass": False,
                    "detail": f"excluded LSOAs leaked: {sorted(lsoas & EXCLUDED_DFES_LSOAS)[:5]}",
                }
            )
            continue
        got = neso_anchors[neso_anchors["LSOA21CD"].isin(lsoas)]
        got_tot = got.groupby(
            ["forecast_scenario", "building_block_id", "anchor_year"], as_index=False
        )["forecast_value"].sum()
        merged = file_tot.merge(
            got_tot,
            left_on=["forecast_scenario", "building_block_id", "year"],
            right_on=["forecast_scenario", "building_block_id", "anchor_year"],
            how="outer",
            suffixes=("_file", "_anchor"),
        )
        merged["abs_diff"] = (merged["forecast_value_file"].fillna(0) - merged["forecast_value_anchor"].fillna(0)).abs()
        max_abs = float(merged["abs_diff"].max()) if not merged.empty else None
        rows.append(
            {
                "check": "SPECIAL_GROUP",
                "group": name,
                "n_lsoa": len(lsoas),
                "n_year_cells": len(merged),
                "max_abs_diff": max_abs,
                "pass": bool(max_abs is not None and max_abs <= FLOAT_TOL),
            }
        )
    return pd.DataFrame(rows)


def qa_hp_actuals_unaltered(actual_dno: pd.DataFrame) -> pd.DataFrame:
    src = pd.read_csv(DASHBOARD_COMPARISON_DNO)
    src = src[(src["tech_type"] == "Heat Pump") & (src["DNO"].isin(["EPN", "LPN", "SPN"]))]
    src = src[(src["period"].astype(str) >= "2025-04") & (src["period"].astype(str) <= "2026-03")]
    src["period"] = src["period"].astype(str)
    actual = actual_dno.copy()
    actual["period"] = actual["period"].astype(str)
    merged = src.merge(
        actual,
        on=["period", "DNO"],
        suffixes=("_src", "_out"),
        how="outer",
        indicator=True,
    )
    both = merged[merged["_merge"] == "both"]
    max_abs = (both["actual_install_count_src"] - both["actual_install_count_out"]).abs().max()
    return pd.DataFrame(
        [
            {
                "check": "HP_ACTUALS_UNALTERED",
                "n_src": len(src),
                "n_out": len(actual),
                "n_matched": len(both),
                "max_abs_diff": float(max_abs) if pd.notna(max_abs) else None,
                "pass": len(src) == len(actual_dno) == len(both)
                and (0 if pd.isna(max_abs) else max_abs) == 0
                and (merged["_merge"] == "both").all(),
            }
        ]
    )


def qa_no_total_bb_created(anchors: pd.DataFrame) -> pd.DataFrame:
    bbs = set(anchors["building_block_id"].dropna().unique())
    details = set(anchors["technology_detail"].dropna().unique())
    forbidden = {"Lct_BB000", "Total", "Total Heat Pumps", "Heat Pump Total"}
    return pd.DataFrame(
        [
            {
                "check": "NO_SYNTHETIC_TOTAL_BB",
                "bbs": ",".join(sorted(str(b) for b in bbs)),
                "details": ",".join(sorted(details)),
                "pass": bbs <= {"Lct_BB005", "Lct_BB006"} and details <= {"Domestic heat pump", "Domestic hybrid heat pump"},
            }
        ]
    )


def qa_year_offset_zero(neso_anchors: pd.DataFrame) -> pd.DataFrame:
    years = set(neso_anchors["anchor_year"].astype(int).unique())
    return pd.DataFrame(
        [
            {
                "check": "YEAR_OFFSET_ZERO",
                "min_year": min(years) if years else None,
                "max_year": max(years) if years else None,
                "pass": years == set(range(2024, 2051)),
            }
        ]
    )


def protected_file_hashes(hash_fn) -> dict[str, str]:
    paths = list(NESO_HP_FILES.values()) + [
        HP_FINAL / "run_summary.csv",
        HP_FINAL / "VALIDATION_STATUS.txt",
        HT_FINAL
        / "HolisticTransition"
        / "Lct_BB001_lsoa_forecast.csv",
        DASHBOARD_COMPARISON_DNO,
        DASHBOARD_COMPARISON_LA,
        DASHBOARD_COMPARISON_LAEP,
    ]
    return {str(p): hash_fn(p) for p in paths if p.exists()}
