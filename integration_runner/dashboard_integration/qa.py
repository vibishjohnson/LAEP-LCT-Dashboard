"""Mandatory NESO and geography QA for the parallel integration."""

from __future__ import annotations

from datetime import date

import pandas as pd

from .config import (
    FLOAT_TOL,
    GEO_TOL,
    HT_ONLY_COUNT,
    NESO_FILES,
    NESO_LSOA_COUNT,
    PUBLISHED_NESO_TOTALS,
)
from .interpolate import interpolate_stock


def neso_file_totals() -> dict[tuple[str, str], float]:
    totals = {}
    for (scenario, bb, _ev), path in NESO_FILES.items():
        raw = pd.read_csv(path, usecols=["forecast_value"])
        totals[(scenario, bb)] = float(raw["forecast_value"].sum())
    return totals


def qa_neso_anchors_unchanged(neso_anchors: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (scenario, bb, ev_type), path in NESO_FILES.items():
        raw = pd.read_csv(path, usecols=["year", "LSOA21CD", "forecast_value"])
        got = neso_anchors[
            (neso_anchors["forecast_scenario"] == scenario)
            & (neso_anchors["building_block_id"] == bb)
        ][["anchor_year", "LSOA21CD", "forecast_value"]].rename(columns={"anchor_year": "year"})
        merged = raw.merge(got, on=["year", "LSOA21CD"], suffixes=("_file", "_anchor"), how="outer", indicator=True)
        matched = merged[merged["_merge"] == "both"]
        extra = merged[merged["_merge"] == "right_only"]
        missing = merged[merged["_merge"] == "left_only"]
        max_abs = (matched["forecast_value_file"] - matched["forecast_value_anchor"]).abs().max()
        extra_ok = extra.empty or float(extra["forecast_value_anchor"].fillna(1).abs().max()) <= FLOAT_TOL
        rows.append({
            "check": "NESO_ANNUAL_EXACT",
            "forecast_scenario": scenario,
            "building_block_id": bb,
            "n_file": len(raw),
            "n_anchor": len(got),
            "n_matched": len(matched),
            "max_abs_diff": float(max_abs) if pd.notna(max_abs) else None,
            "pass": len(missing) == 0 and extra_ok and (0 if pd.isna(max_abs) else max_abs) <= FLOAT_TOL,
        })
    return pd.DataFrame(rows)


def qa_published_totals(neso_anchors: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (scenario, bb), expected in PUBLISHED_NESO_TOTALS.items():
        got = neso_anchors[
            (neso_anchors["forecast_scenario"] == scenario)
            & (neso_anchors["building_block_id"] == bb)
        ]["forecast_value"].sum()
        rows.append({
            "check": "NESO_PUBLISHED_TOTAL",
            "forecast_scenario": scenario,
            "building_block_id": bb,
            "expected_rounded": expected,
            "actual_sum": float(got),
            "rounded_actual": int(round(got)),
            "pass": int(round(got)) == expected,
        })
    return pd.DataFrame(rows)


def qa_anchor_dates(anchors: pd.DataFrame) -> pd.DataFrame:
    years = anchors["anchor_year"].astype(int)
    expected = years.map(lambda y: date(y + 1, 3, 31))
    got = pd.to_datetime(anchors["anchor_date"]).dt.date
    ok = (got == expected).all()
    return pd.DataFrame([{
        "check": "ANCHOR_DATE_31_MAR_Y_PLUS_1",
        "n_rows": len(anchors),
        "n_bad": int((got != expected).sum()),
        "pass": bool(ok),
    }])


def qa_lsoa_universe(geography: pd.DataFrame, neso_anchors: pd.DataFrame) -> pd.DataFrame:
    neso_geo = geography[geography["in_neso_spatial"]]
    ht_only = geography[geography["geography_flag"] == "HT_ONLY_BOUNDARY"]
    dash_only = geography[geography["geography_flag"] == "NESO_SPATIAL_NOT_ASSIGNED"]
    neso_lsoas = set(neso_anchors["LSOA21CD"])
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
            "check": "HT_ONLY_VOLUME",
            "expected": 0.0,
            "actual": float(neso_anchors[neso_anchors["LSOA21CD"].isin(ht_only["LSOA21CD"])]["forecast_value"].sum()),
            "pass": abs(float(neso_anchors[neso_anchors["LSOA21CD"].isin(ht_only["LSOA21CD"])]["forecast_value"].sum())) <= FLOAT_TOL,
        },
    ]
    return pd.DataFrame(rows)


def qa_monthly_anchor_lsoa(neso_anchor_df: pd.DataFrame) -> pd.DataFrame:
    if neso_anchor_df.empty:
        return pd.DataFrame([{"check": "NESO_MONTHLY_LSOA_ANCHOR", "pass": False, "detail": "empty"}])
    max_abs = float(neso_anchor_df["abs_diff"].max())
    n_bad = int((neso_anchor_df["abs_diff"] > FLOAT_TOL).sum())
    n_null = int(neso_anchor_df["monthly_value"].isna().sum() + neso_anchor_df["forecast_value"].isna().sum())
    return pd.DataFrame([{
        "check": "NESO_MONTHLY_LSOA_ANCHOR",
        "n_rows": len(neso_anchor_df),
        "n_bad": n_bad,
        "n_null": n_null,
        "max_abs_diff": max_abs,
        "pass": n_bad == 0 and n_null == 0 and max_abs <= FLOAT_TOL,
    }])


def qa_monthly_anchor_totals(monthly_dno: pd.DataFrame, neso_anchors: pd.DataFrame) -> pd.DataFrame:
    rows = []
    neso_m = monthly_dno[(monthly_dno["forecast_source"] == "NESO") & (monthly_dno["is_anchor"])]
    annual = neso_anchors.groupby(
        ["forecast_scenario", "building_block_id", "anchor_year", "EV_Type"],
        as_index=False,
    )["forecast_value"].sum()
    annual["period"] = annual["anchor_year"].map(lambda y: f"{int(y) + 1}-03")
    monthly = neso_m.groupby(
        ["forecast_scenario", "building_block_id", "period", "EV_Type"],
        as_index=False,
    )["forecast_value"].sum()
    merged = annual.merge(
        monthly,
        on=["forecast_scenario", "building_block_id", "period", "EV_Type"],
        suffixes=("_annual", "_monthly"),
        how="outer",
    )
    merged["abs_diff"] = (merged["forecast_value_annual"] - merged["forecast_value_monthly"]).abs()
    for (scenario, bb), part in merged.groupby(["forecast_scenario", "building_block_id"]):
        rows.append({
            "check": "NESO_MONTHLY_ANCHOR_TOTAL",
            "forecast_scenario": scenario,
            "building_block_id": bb,
            "n_years": part["anchor_year"].nunique(),
            "max_abs_diff": float(part["abs_diff"].max()) if part["abs_diff"].notna().any() else None,
            "pass": bool(part["abs_diff"].fillna(1).max() <= FLOAT_TOL),
        })
    return pd.DataFrame(rows)


def qa_geography_reconciliation(coverage: pd.DataFrame) -> pd.DataFrame:
    coverage = coverage.copy()
    coverage["lsoa_vs_la"] = (coverage["lsoa_total"] - coverage["la_total"]).abs()
    coverage["lsoa_vs_dno"] = (coverage["lsoa_total"] - coverage["dno_total"]).abs()
    coverage["laep_share"] = coverage["laep_total"] / coverage["lsoa_total"].replace(0, pd.NA)
    neso = coverage[coverage["forecast_source"] == "NESO"]
    return pd.DataFrame([
        {
            "check": "GEO_LSOA_EQ_LA",
            "max_abs_diff": float(neso["lsoa_vs_la"].max()) if not neso.empty else None,
            "pass": bool(neso["lsoa_vs_la"].max() <= GEO_TOL) if not neso.empty else False,
        },
        {
            "check": "GEO_LSOA_EQ_DNO",
            "max_abs_diff": float(neso["lsoa_vs_dno"].max()) if not neso.empty else False,
            "pass": bool(neso["lsoa_vs_dno"].max() <= GEO_TOL) if not neso.empty else False,
        },
        {
            "check": "GEO_LAEP_INCOMPLETE_DOCUMENTED",
            "min_laep_share": float(neso["laep_share"].min()) if neso["laep_share"].notna().any() else None,
            "max_laep_share": float(neso["laep_share"].max()) if neso["laep_share"].notna().any() else None,
            "pass": bool((neso["laep_total"] <= neso["lsoa_total"] + GEO_TOL).all()) if not neso.empty else False,
        },
    ])


def qa_source_distinction(anchors: pd.DataFrame) -> pd.DataFrame:
    combos = anchors.groupby(["forecast_source", "forecast_scenario", "EV_Type"]).size().reset_index(name="n")
    has_dfes_ht = ((combos["forecast_source"] == "DFES") & (combos["forecast_scenario"] == "HolisticTransition")).any()
    has_neso_ht = ((combos["forecast_source"] == "NESO") & (combos["forecast_scenario"] == "HolisticTransition")).any()
    has_neso_htb = ((combos["forecast_source"] == "NESO") & (combos["forecast_scenario"] == "HolisticTransitionBasic")).any()
    return pd.DataFrame([{
        "check": "SOURCE_SCENARIO_DISTINCT",
        "dfes_ht": bool(has_dfes_ht),
        "neso_ht": bool(has_neso_ht),
        "neso_htb": bool(has_neso_htb),
        "n_combos": len(combos),
        "pass": bool(has_dfes_ht and has_neso_ht and has_neso_htb),
    }])


def qa_interpolation_line() -> pd.DataFrame:
    t0 = date(2025, 3, 31)
    t1 = date(2026, 3, 31)
    v0, v1 = 100.0, 200.0
    mid = date(2025, 9, 30)
    expected = interpolate_stock(mid, t0, v0, t1, v1)
    exact_start = interpolate_stock(t0, t0, v0, t1, v1)
    exact_end = interpolate_stock(t1, t0, v0, t1, v1)
    leap_t0 = date(2024, 3, 31)
    leap_t1 = date(2025, 3, 31)
    leap_mid = date(2024, 8, 31)
    leap_val = interpolate_stock(leap_mid, leap_t0, 0.0, leap_t1, 365.0)
    leap_expected = (leap_mid - leap_t0).days
    return pd.DataFrame([
        {"check": "INTERP_ENDPOINT_START", "expected": v0, "actual": exact_start, "pass": exact_start == v0},
        {"check": "INTERP_ENDPOINT_END", "expected": v1, "actual": exact_end, "pass": exact_end == v1},
        {
            "check": "INTERP_MID_ON_LINE",
            "expected": v0 + (v1 - v0) * ((mid - t0).days / (t1 - t0).days),
            "actual": expected,
            "pass": abs(expected - (v0 + (v1 - v0) * ((mid - t0).days / (t1 - t0).days))) <= FLOAT_TOL,
        },
        {
            "check": "INTERP_LEAP_DAYS",
            "expected": float(leap_expected),
            "actual": leap_val,
            "pass": abs(leap_val - leap_expected) <= FLOAT_TOL,
        },
    ])


def qa_no_rounding(monthly: pd.DataFrame) -> pd.DataFrame:
    neso = monthly[monthly["forecast_source"] == "NESO"]
    frac = (~neso["forecast_value"].apply(lambda x: float(x).is_integer())).mean() if not neso.empty else 0
    return pd.DataFrame([{
        "check": "FRACTIONAL_VALUES_PRESENT",
        "fraction_non_integer": float(frac),
        "pass": bool(frac > 0),
    }])
