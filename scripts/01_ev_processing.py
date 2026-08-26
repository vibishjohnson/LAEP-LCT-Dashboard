#!/usr/bin/env python3
"""
EV Processing Pipeline

Goal:
- Produce BEV and PHEV counts by body type (Cars, Vans, HGV, Buses, Motorcycles, Other)
  at:
    1) LSOA21 level
    2) LAEP level

Inputs (run from project root):
- ev/df_VEH0135.csv
- ev/VEH0142.csv
- ev/mapping file.csv      (OA11CD -> LSOA11CD, LAD11CD)
- lookups/lsoa11_to_lsoa21_lad22.csv
- lookups/lad22_to_laep.csv

Outputs (project/output_processed/):
- lsoa_ev_bodytype.csv
- laep_ev_bodytype.csv
- lad_ev_bodytype_proportions.csv
- ev_missing_geo.csv
- ev_qa_summary.txt (optional summary also printed to console)
"""

import os
import re
import math
import warnings
from typing import List, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

FULL_SERIES = False  # If True, process all quarters
LAST_N_QUARTERS = 2  # Used when FULL_SERIES is False
START_QUARTER = "2025-03-31"  # Q1 2025 onwards (fiscal year start)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EV_DIR = os.path.join(PROJECT_ROOT, "ev")
LOOKUPS_DIR = os.path.join(PROJECT_ROOT, "lookups")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "project", "output_processed")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def select_keepership_total_if_present(df: pd.DataFrame, keepership_col: str) -> Tuple[pd.DataFrame, List[str]]:
    """If any keepership value contains 'TOTAL' (case-insensitive), keep only those; otherwise keep all."""
    if keepership_col not in df.columns:
        return df, []
    ks = df[keepership_col].astype(str)
    unique_vals = sorted(set(ks.str.strip()))
    has_total = ks.str.contains("total", case=False, na=False)
    used = []
    if has_total.any():
        df = df[has_total].copy()
        used = sorted(set(df[keepership_col].astype(str).str.strip()))
    return df, unique_vals if not used else used


def detect_quarter_columns(columns: List[str]) -> List[str]:
    """Detect quarter columns like '2025 Q2'."""
    quarter_re = re.compile(r"^\d{4}\sQ[1-4]$")
    return [c for c in columns if quarter_re.match(str(c))]


def quarter_label_to_period(quarter_label: str) -> str:
    """Convert 'YYYY Qn' -> quarter-end date string."""
    year_str, q_str = quarter_label.split()
    year = int(year_str)
    q = int(q_str[1])
    month_day = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}[q]
    return f"{year}-{month_day}"


def clean_numeric_series(s: pd.Series) -> pd.Series:
    """Clean numeric string series: remove commas, convert non-numeric to NaN."""
    s = s.astype(str).str.replace(",", "", regex=False).str.strip()
    # Replace known non-numeric markers
    s = s.replace({"[x]": np.nan, "[c]": np.nan})
    return pd.to_numeric(s, errors="coerce")


def largest_remainder_round(values: np.ndarray, target_total: float) -> np.ndarray:
    """
    Largest remainder method:
    - Floor all values
    - Distribute remaining units to largest fractional parts to match target_total.
    """
    if len(values) == 0:
        return values
    floors = np.floor(values)
    fracs = values - floors
    floor_sum = floors.sum()
    remaining = int(round(target_total - floor_sum))

    if remaining > 0:
        # Distribute +1 to those with largest fractional parts
        order = np.argsort(-fracs)  # descending
        for idx in order[:remaining]:
            floors[idx] += 1
    elif remaining < 0:
        # Remove units from smallest fractional parts
        order = np.argsort(fracs)  # ascending
        for idx in order[: -remaining]:
            if floors[idx] > 0:
                floors[idx] -= 1
    return floors.astype(int)


def redistribute_lsoa_totals(df: pd.DataFrame) -> pd.DataFrame:
    """
    Fleet redistribution (per period, Fuel):
      base = min(value, 100)
      pool = sum(max(value - 100, 0))
      weights = base / base.sum()
      redistributed = base + pool * weights
      Rounded with largest remainder, preserving totals.
    """
    def _redistribute_group(g: pd.DataFrame) -> pd.DataFrame:
        vals = g["value"].to_numpy(dtype=float)
        base = np.minimum(vals, 100.0)
        pool = float(np.maximum(vals - 100.0, 0.0).sum())
        base_sum = base.sum()
        if base_sum <= 0 or pool <= 0:
            # Nothing to redistribute; just round original values
            rounded = largest_remainder_round(vals, vals.sum())
            g = g.copy()
            g["redistributed"] = rounded
            return g
        weights = base / base_sum
        raw = base + pool * weights
        redistributed = largest_remainder_round(raw, vals.sum())
        g = g.copy()
        g["redistributed"] = redistributed
        return g

    return df.groupby(["period", "Fuel"], group_keys=False).apply(_redistribute_group)


def map_fuel_to_ev(fuel_series: pd.Series) -> pd.Series:
    """Map raw Fuel strings to 'BEV' or 'PHEV' (filtering to just those)."""
    f = fuel_series.astype(str).str.upper()
    is_bev = f == "BATTERY ELECTRIC"
    is_phev = (~is_bev) & (f.str.contains("PLUG", na=False) | f.str.contains("HYBRID", na=False))
    mapped = pd.Series(index=f.index, dtype="object")
    mapped[is_bev] = "BEV"
    mapped[is_phev] = "PHEV"
    return mapped


BODYTYPE_MAP = {
    "light goods vehicles": "Vans",
    "heavy goods vehicles": "HGV",
    "buses and coaches": "Buses",
    "cars": "Cars",
    "motorcycles": "Motorcycles",
    "other vehicles": "Other",
}


def standardise_bodytype(bodytype: pd.Series) -> pd.Series:
    bt = bodytype.astype(str)
    lower = bt.str.lower().str.strip()
    mapped = lower.map(BODYTYPE_MAP)
    # Keep original (title-cased) if not in mapping and not 'Total'
    mapped = mapped.where(~mapped.isna(), bt.str.title())
    # Drop 'Total' later, but normalise text
    return mapped


def load_bridge_lsoa11_to_21_lad22() -> pd.DataFrame:
    """
    Load LSOA11 -> (LSOA21, LAD22) bridge.

    Uses your real file:
      lookups/LSOA_(2011)_to_LSOA_(2021)_to_Local_Authority_District_(2022)_Exact_Fit_Lookup_for_EW_(V3).csv
    """
    path = os.path.join(
        LOOKUPS_DIR,
        "LSOA_(2011)_to_LSOA_(2021)_to_Local_Authority_District_(2022)_Exact_Fit_Lookup_for_EW_(V3).csv",
    )
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing LSOA11→LSOA21→LAD22 bridge at {path}")

    df = pd.read_csv(path)

    # Identify key columns by name (case-insensitive)
    l11_col = None
    l21_col = None
    lad_col = None
    for c in df.columns:
        lc = c.lower()
        if lc == "lsoa11cd":
            l11_col = c
        elif lc == "lsoa21cd":
            l21_col = c
        elif lc == "lad22cd":
            lad_col = c

    # Allow fuzzy detection if needed
    if l11_col is None:
        for c in df.columns:
            if "lsoa11" in c.lower():
                l11_col = c
                break
    if l21_col is None:
        for c in df.columns:
            if "lsoa21" in c.lower():
                l21_col = c
                break
    if lad_col is None:
        for c in df.columns:
            if "lad22" in c.lower():
                lad_col = c
                break

    if l11_col is None or l21_col is None or lad_col is None:
        raise ValueError("Bridge file is missing LSOA11CD / LSOA21CD / LAD22CD columns.")

    out = df[[l11_col, l21_col, lad_col]].copy()
    out.columns = ["LSOA11CD", "LSOA21CD", "LAD22CD"]
    for c in ["LSOA11CD", "LSOA21CD", "LAD22CD"]:
        out[c] = out[c].astype(str).str.strip()
    return out.drop_duplicates()


def load_lad22_to_laep() -> pd.DataFrame:
    # Per spec, use lad22_to_laep.csv (ID_CODE, LAEP)
    path = os.path.join(LOOKUPS_DIR, "lad22_to_laep.csv")
    if not os.path.exists(path):
        # Fallback: LA-LAEP lookup.csv (used elsewhere)
        alt = os.path.join(LOOKUPS_DIR, "LA-LAEP lookup.csv")
        if not os.path.exists(alt):
            raise FileNotFoundError(f"Missing lad22_to_laep.csv (or LA-LAEP lookup.csv) in {LOOKUPS_DIR}")
        df_raw = pd.read_csv(alt)
        id_col = None
        laep_col = None
        for c in df_raw.columns:
            lc = c.lower()
            if lc == "id_code":
                id_col = c
            elif lc == "laep":
                laep_col = c
        if id_col is None or laep_col is None:
            raise ValueError("LA-LAEP lookup must contain 'ID_CODE' and 'LAEP' columns.")
        df = df_raw[[id_col, laep_col]].drop_duplicates()
    else:
        df_raw = pd.read_csv(path)
        id_col = None
        laep_col = None
        for c in df_raw.columns:
            lc = c.lower()
            if lc == "id_code":
                id_col = c
            elif lc == "laep":
                laep_col = c
        if id_col is None or laep_col is None:
            raise ValueError("lad22_to_laep.csv must contain 'ID_CODE' and 'LAEP' columns.")
        df = df_raw[[id_col, laep_col]].drop_duplicates()

    df = df.rename(columns={df.columns[0]: "LAD22CD", df.columns[1]: "LAEP"})
    df["LAD22CD"] = df["LAD22CD"].astype(str).str.strip()
    df["LAEP"] = df["LAEP"].astype(str).str.strip()
    return df


def load_lsoa21_to_ukpn() -> pd.DataFrame:
    """
    Load LSOA21 -> UKPN licence area mapping from:
      ev/LSOA mapping.xlsx
    Expected key columns:
      - LSOA21CD
      - Majority Licence area
    """
    path = os.path.join(EV_DIR, "LSOA mapping.xlsx")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing UKPN mapping workbook at {path}")

    df = pd.read_excel(path)
    # Detect columns robustly
    lsoa_col = None
    ukpn_col = None
    for c in df.columns:
        lc = str(c).lower().strip()
        if lc == "lsoa21cd":
            lsoa_col = c
        elif "majority licence area" in lc:
            ukpn_col = c

    if lsoa_col is None:
        raise ValueError("LSOA mapping.xlsx is missing LSOA21CD column.")
    if ukpn_col is None:
        raise ValueError("LSOA mapping.xlsx is missing 'Majority Licence area' column.")

    out = df[[lsoa_col, ukpn_col]].drop_duplicates().copy()
    out.columns = ["LSOA21CD", "UKPN"]
    out["LSOA21CD"] = out["LSOA21CD"].astype(str).str.strip()
    out["UKPN"] = out["UKPN"].astype(str).str.strip()
    # Clean empty strings to NaN
    out.loc[out["UKPN"] == "", "UKPN"] = np.nan
    return out


# ---------------------------------------------------------------------------
# Step 1: VEH0135 – LSOA fuel totals with redistribution
# ---------------------------------------------------------------------------

def process_veh0135(bridge: pd.DataFrame) -> Tuple[pd.DataFrame, dict]:
    path = os.path.join(EV_DIR, "df_VEH0135.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing df_VEH0135.csv at {path}")

    df = pd.read_csv(path)

    # Keepership
    df, used_keepership = select_keepership_total_if_present(df, "Keepership")

    # Support either LSOA11CD or LSOA21CD in updated extracts
    if "LSOA11CD" in df.columns:
        lsoa_col = "LSOA11CD"
    elif "LSOA21CD" in df.columns:
        lsoa_col = "LSOA21CD"
    else:
        raise ValueError("df_VEH0135.csv must contain either LSOA11CD or LSOA21CD")

    # Filter to BEV/PHEV (overwrite Fuel with mapped EV fuel to avoid duplicate columns)
    df["Fuel"] = map_fuel_to_ev(df["Fuel"])
    df = df[df["Fuel"].notna()].copy()

    # Melt quarters
    quarter_cols = detect_quarter_columns(df.columns.tolist())
    if not quarter_cols:
        raise ValueError("No quarter columns detected in df_VEH0135.csv")

    # Manually "melt" to avoid pandas melt bugs on this wide table
    rows = []
    for q in quarter_cols:
        tmp = df[[lsoa_col, "Fuel", q]].copy()
        tmp = tmp.rename(columns={lsoa_col: "LSOA_KEY"})
        tmp = tmp.rename(columns={q: "value_raw"})
        tmp["period_label"] = q
        rows.append(tmp)
    df_long = pd.concat(rows, ignore_index=True)

    # Convert to canonical LSOA11CD used downstream
    if lsoa_col == "LSOA11CD":
        df_long["LSOA11CD"] = df_long["LSOA_KEY"].astype(str).str.strip()
    else:
        # lsoa_col is LSOA21CD; derive LSOA11CD via bridge
        bridge_lsoa = bridge[["LSOA11CD", "LSOA21CD"]].drop_duplicates().copy()
        bridge_lsoa["LSOA21CD"] = bridge_lsoa["LSOA21CD"].astype(str).str.strip()
        df_long["LSOA21CD"] = df_long["LSOA_KEY"].astype(str).str.strip()
        df_long = df_long.merge(bridge_lsoa, on="LSOA21CD", how="left")

    # Clean numeric
    df_long["value"] = clean_numeric_series(df_long["value_raw"]).fillna(0)

    # Period
    df_long["period"] = df_long["period_label"].apply(quarter_label_to_period)

    # Filter to quarters from START_QUARTER onwards
    all_periods = sorted(df_long["period"].unique())
    selected_periods = all_periods  # Default: all periods
    if not FULL_SERIES:
        selected_periods = [p for p in all_periods if p >= START_QUARTER]
        df_long = df_long[df_long["period"].isin(selected_periods)].copy()
        print(f"  Filtered to periods >= {START_QUARTER}: {sorted(selected_periods)}")

    latest_period = max(selected_periods) if selected_periods else max(all_periods)

    # Fleet redistribution
    agg_before = df_long.groupby("Fuel")["value"].sum().to_dict()
    capped_share = (
        (df_long.assign(above_100=lambda d: d["value"] > 100).groupby("Fuel")["above_100"].mean()).to_dict()
    )

    df_redist = redistribute_lsoa_totals(df_long[["LSOA11CD", "Fuel", "period", "value"]].copy())
    agg_after = df_redist.groupby("Fuel")["redistributed"].sum().to_dict()

    # Pivot to wide: BEV_total, PHEV_total
    df_pivot = (
        df_redist.pivot_table(
            index=["LSOA11CD", "period"], columns="Fuel", values="redistributed", aggfunc="sum", fill_value=0
        )
        .reset_index()
    )
    # Ensure BEV/PHEV columns exist
    for col in ["BEV", "PHEV"]:
        if col not in df_pivot.columns:
            df_pivot[col] = 0
    df_pivot = df_pivot.rename(columns={"BEV": "BEV_total", "PHEV": "PHEV_total"})

    stats = {
        "keepership_used": used_keepership,
        "periods_available": all_periods,
        "latest_period": latest_period,
        "selected_periods": selected_periods if not FULL_SERIES else all_periods,
        "totals_before": agg_before,
        "totals_after": agg_after,
        "capped_share": capped_share,
    }
    return df_pivot, stats


# ---------------------------------------------------------------------------
# Step 3: VEH0142 – LAD22 body-type proportions
# ---------------------------------------------------------------------------

def process_veh0142(bridge: pd.DataFrame) -> Tuple[pd.DataFrame, dict]:
    """
    Process VEH0142, which is already at LAD level (ONS Code = LAD code).
    Build BEV/PHEV body-type proportions by LAD22CD + period + Fuel.
    """
    path = os.path.join(EV_DIR, "VEH0142.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Missing VEH0142.csv at {path}")

    df = pd.read_csv(path)

    # Keepership
    df, used_keepership = select_keepership_total_if_present(df, "Keepership")

    # Fuel filter (overwrite Fuel with mapped EV fuel to avoid duplicate columns)
    df["Fuel"] = map_fuel_to_ev(df["Fuel"])
    df = df[df["Fuel"].notna()].copy()

    # Body type mapping
    df["BodyType_std"] = standardise_bodytype(df["BodyType"])
    # Drop total rows
    df = df[~df["BodyType_std"].str.lower().eq("total")].copy()

    # Melt quarters
    quarter_cols = detect_quarter_columns(df.columns.tolist())
    if not quarter_cols:
        raise ValueError("No quarter columns detected in VEH0142.csv")

    # ONS Code is LAD-level (treat as LAD22CD directly)
    df = df.rename(columns={"ONS Code": "LAD22CD"})
    df["LAD22CD"] = df["LAD22CD"].astype(str).str.strip()

    id_cols = ["LAD22CD", "Fuel", "BodyType_std"]
    df_long = df[id_cols + quarter_cols].melt(id_vars=id_cols, var_name="period_label", value_name="value_raw")

    df_long["value"] = clean_numeric_series(df_long["value_raw"]).fillna(0)
    df_long["period"] = df_long["period_label"].apply(quarter_label_to_period)

    all_periods = sorted(df_long["period"].unique())
    latest_period = max(all_periods)
    selected_periods = all_periods  # Default: all periods
    if not FULL_SERIES:
        selected_periods = [p for p in all_periods if p >= START_QUARTER]
        df_long = df_long[df_long["period"].isin(selected_periods)].copy()
        print(f"  Filtered to periods >= {START_QUARTER}: {sorted(selected_periods)}")

    # Aggregate to LAD22CD + period + Fuel + BodyType
    grouped = (
        df_long.groupby(["LAD22CD", "period", "Fuel", "BodyType_std"], as_index=False)["value"].sum()
    )

    # Compute proportions within LAD22CD + period + Fuel
    def _compute_props(g: pd.DataFrame) -> pd.DataFrame:
        total = g["value"].sum()
        if total <= 0:
            g = g.copy()
            g["prop"] = 0.0
            return g
        g = g.copy()
        g["prop"] = g["value"] / total
        # Renormalise for any numeric drift
        s = g["prop"].sum()
        if s > 0:
            g["prop"] = g["prop"] / s
        return g

    if grouped.empty:
        props = pd.DataFrame(columns=["LAD22CD", "period", "Fuel", "BodyType_std", "value", "prop"])
    else:
        props = grouped.groupby(["LAD22CD", "period", "Fuel"], group_keys=False).apply(_compute_props)
        props = props.reset_index(drop=True)
        if "prop" not in props.columns:
            props["prop"] = 0.0

    # QA output
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    qa_path = os.path.join(OUTPUT_DIR, "lad_ev_bodytype_proportions.csv")
    props.rename(columns={"BodyType_std": "BodyType"}, inplace=True)
    props.to_csv(qa_path, index=False)

    stats = {
        "keepership_used": used_keepership,
        "periods_available": all_periods,
        "latest_period": latest_period,
        "selected_periods": selected_periods if not FULL_SERIES else all_periods,
    }

    return props[["LAD22CD", "period", "Fuel", "BodyType", "prop"]], stats


# ---------------------------------------------------------------------------
# Step 4: Apply proportions to redistributed LSOA totals
# ---------------------------------------------------------------------------

def allocate_lsoa_bodytypes(
    lsoa_totals: pd.DataFrame, bridge: pd.DataFrame, props: pd.DataFrame
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    lsoa_totals: columns LSOA11CD, period, BEV_total, PHEV_total
    props: LAD22CD, period, Fuel, BodyType, prop
    Returns:
      - lsoa_body: LSOA21CD, LAD22CD, period, Fuel, BodyType, ev_count
      - missing_geo_df: rows with missing geo/LAEP later
    """
    # Map LSOA11 -> LSOA21 + LAD22
    lsoa_totals = lsoa_totals.copy()
    lsoa_totals["LSOA11CD"] = lsoa_totals["LSOA11CD"].astype(str).str.strip()
    bridge = bridge.copy()
    bridge["LSOA11CD"] = bridge["LSOA11CD"].astype(str).str.strip()
    # Prevent fanout: enforce one row per LSOA11CD for allocation joins
    bridge_one = bridge.drop_duplicates(subset=["LSOA11CD"], keep="first").copy()

    lsoa_geo = lsoa_totals.merge(bridge_one, on="LSOA11CD", how="left")

    # Melt BEV_total/PHEV_total to long Fuel
    fuel_long = lsoa_geo.melt(
        id_vars=["LSOA11CD", "LSOA21CD", "LAD22CD", "period"],
        value_vars=["BEV_total", "PHEV_total"],
        var_name="Fuel_raw",
        value_name="total",
    )
    fuel_long["Fuel"] = fuel_long["Fuel_raw"].str.replace("_total", "", regex=False)
    fuel_long["total"] = fuel_long["total"].fillna(0).astype(float)

    # Join proportions on LAD22CD + period + Fuel
    # Ensure props has no index levels named LAD22CD/period/Fuel to avoid ambiguity
    props_keyed = props.reset_index(drop=True).rename(
        columns={"period": "period", "Fuel": "Fuel", "BodyType": "BodyType"}
    )
    alloc = fuel_long.merge(
        props_keyed,
        on=["LAD22CD", "period", "Fuel"],
        how="left",
    )

    # Some LAD22+Fuel combos might have no proportions -> prop NaN
    # For those, we leave them for missing_geo handling
    alloc_valid = alloc[alloc["prop"].notna()].copy()
    alloc_missing_prop = alloc[alloc["prop"].isna()].copy()

    # Largest remainder within each LSOA+Fuel group to allocate integer counts
    def _alloc_group(g: pd.DataFrame) -> pd.DataFrame:
        total = float(g["total"].iloc[0])
        if total <= 0:
            g = g.copy()
            g["ev_count"] = 0
            return g
        raw = g["total"] * g["prop"]
        ev_counts = largest_remainder_round(raw.to_numpy(dtype=float), total)
        g = g.copy()
        g["ev_count"] = ev_counts
        return g

    if alloc_valid.empty:
        allocated = pd.DataFrame(
            columns=["LSOA11CD", "LSOA21CD", "LAD22CD", "period", "Fuel", "BodyType", "ev_count"]
        )
    else:
        allocated = (
            alloc_valid.groupby(
                ["LSOA11CD", "LSOA21CD", "LAD22CD", "period", "Fuel"], group_keys=False
            ).apply(_alloc_group)
        )
        # Make sure ev_count column exists even if all totals were zero
        if "ev_count" not in allocated.columns:
            allocated["ev_count"] = 0

    # Build final LSOA bodytype table
    lsoa_body = allocated[
        ["period", "LSOA11CD", "LSOA21CD", "LAD22CD", "Fuel", "BodyType", "ev_count"]
    ].copy()

    # Missing geo: anything with missing LSOA21CD or LAD22CD, plus any totals with no props
    miss_geo_rows = []
    miss_geo_rows.append(
        lsoa_body[lsoa_body["LSOA21CD"].isna() | lsoa_body["LAD22CD"].isna()].copy()
    )
    if not alloc_missing_prop.empty:
        tmp = alloc_missing_prop[
            ["period", "LSOA11CD", "LSOA21CD", "LAD22CD", "Fuel", "total"]
        ].drop_duplicates()
        tmp = tmp.rename(columns={"total": "ev_count"})
        tmp["BodyType"] = "UNALLOCATED"
        miss_geo_rows.append(tmp)

    missing_geo_df = pd.concat(miss_geo_rows, ignore_index=True) if miss_geo_rows else pd.DataFrame()

    return lsoa_body, missing_geo_df


# ---------------------------------------------------------------------------
# Step 5: Aggregate to LAEP and write outputs
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("EV Processing – BEV/PHEV by body type")
    print("=" * 60)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Load lookup tables
    bridge = load_bridge_lsoa11_to_21_lad22()
    lad22_laep = load_lad22_to_laep()
    lsoa_ukpn = load_lsoa21_to_ukpn()

    # Step 1 & 2: VEH0135 totals + redistribution
    lsoa_totals, stats_0135 = process_veh0135(bridge)
    print("\n--- VEH0135 (LSOA totals) ---")
    print(f"Keepership used: {stats_0135['keepership_used']}")
    print(f"Available periods: {stats_0135['periods_available']}")
    print(f"Latest period: {stats_0135['latest_period']}")
    print(f"Totals before redistribution: {stats_0135['totals_before']}")
    print(f"Totals after redistribution:  {stats_0135['totals_after']}")
    print(f"Share of LSOAs capped >100 (by Fuel): {stats_0135['capped_share']}")

    # Step 3: VEH0142 body-type proportions by LAD22
    props, stats_0142 = process_veh0142(bridge)
    print("\n--- VEH0142 (LAD22 body-type proportions) ---")
    print(f"Keepership used: {stats_0142['keepership_used']}")
    print(f"Available periods: {stats_0142['periods_available']}")
    print(f"Latest period: {stats_0142['latest_period']}")

    # Step 4: Allocate to LSOA by body type
    lsoa_body, missing_geo_df = allocate_lsoa_bodytypes(lsoa_totals, bridge, props)

    # Keep geometry from allocation step (already joined to bridge without fanout)
    lsoa_body = lsoa_body[
        ["period", "LSOA11CD", "LSOA21CD", "LAD22CD", "Fuel", "BodyType", "ev_count"]
    ].copy()

    # Write LSOA output
    lsoa_out = lsoa_body.dropna(subset=["LSOA21CD", "LAD22CD"]).copy()
    lsoa_out_path = os.path.join(OUTPUT_DIR, "lsoa_ev_bodytype.csv")
    lsoa_out.to_csv(lsoa_out_path, index=False)
    print(f"\nWritten LSOA EV bodytype file: {lsoa_out_path} ({len(lsoa_out)} rows)")

    # Step 5: LAEP aggregation
    lsoa_laep = lsoa_out.merge(lad22_laep, on="LAD22CD", how="left")
    missing_laep = lsoa_laep["LAEP"].isna().sum()
    laep_out = lsoa_laep.dropna(subset=["LAEP"]).copy()

    laep_agg = (
        laep_out.groupby(["period", "LAEP", "Fuel", "BodyType"], as_index=False)["ev_count"].sum()
    )
    laep_out_path = os.path.join(OUTPUT_DIR, "laep_ev_bodytype.csv")
    laep_agg.to_csv(laep_out_path, index=False)
    print(f"Written LAEP EV bodytype file: {laep_out_path} ({len(laep_agg)} rows)")

    # UKPN aggregation (using LSOA21 -> Majority Licence area)
    lsoa_ukpn_df = lsoa_out.merge(lsoa_ukpn, on="LSOA21CD", how="left")
    ukpn_missing = lsoa_ukpn_df["UKPN"].isna().sum()
    ukpn_out = lsoa_ukpn_df.dropna(subset=["UKPN"]).copy()
    ukpn_agg = (
        ukpn_out.groupby(["period", "UKPN", "Fuel", "BodyType"], as_index=False)["ev_count"].sum()
    )
    ukpn_out_path = os.path.join(OUTPUT_DIR, "ukpn_ev_bodytype.csv")
    ukpn_agg.to_csv(ukpn_out_path, index=False)
    print(f"Written UKPN EV bodytype file: {ukpn_out_path} ({len(ukpn_agg)} rows)")

    # Missing geo file
    miss_geo_parts = []
    if not missing_geo_df.empty:
        miss_geo_parts.append(missing_geo_df)
    # LSOA records missing LAEP
    if missing_laep > 0:
        tmp = lsoa_laep[lsoa_laep["LAEP"].isna()][
            ["period", "LSOA11CD", "LSOA21CD", "LAD22CD", "Fuel", "BodyType", "ev_count"]
        ].copy()
        miss_geo_parts.append(tmp)
    # LSOA records missing UKPN
    if ukpn_missing > 0:
        tmp = lsoa_ukpn_df[lsoa_ukpn_df["UKPN"].isna()][
            ["period", "LSOA11CD", "LSOA21CD", "LAD22CD", "Fuel", "BodyType", "ev_count"]
        ].copy()
        miss_geo_parts.append(tmp)

    if miss_geo_parts:
        ev_missing = pd.concat(miss_geo_parts, ignore_index=True)
    else:
        ev_missing = pd.DataFrame(
            columns=["period", "LSOA11CD", "LSOA21CD", "LAD22CD", "Fuel", "BodyType", "ev_count"]
        )

    missing_path = os.path.join(OUTPUT_DIR, "ev_missing_geo.csv")
    ev_missing.to_csv(missing_path, index=False)
    print(f"Written EV missing geo file: {missing_path} ({len(ev_missing)} rows)")

    # QA summary text
    qa_lines = []
    qa_lines.append("EV Processing QA Summary")
    qa_lines.append("=" * 40)
    qa_lines.append(f"Latest period (0135): {stats_0135['latest_period']}")
    qa_lines.append(f"Totals before redistribution: {stats_0135['totals_before']}")
    qa_lines.append(f"Totals after redistribution:  {stats_0135['totals_after']}")
    qa_lines.append(f"Share of LSOAs capped >100: {stats_0135['capped_share']}")
    qa_lines.append("")
    qa_lines.append(f"VEH0142 latest period: {stats_0142['latest_period']}")
    qa_lines.append("")
    qa_lines.append(f"LSOA body rows: {len(lsoa_out)}")
    qa_lines.append(f"LAEP body rows: {len(laep_agg)}")
    qa_lines.append(f"UKPN body rows: {len(ukpn_agg)}")
    qa_lines.append(f"Missing geo rows: {len(ev_missing)} (incl. missing LAEP)")
    qa_lines.append("")

    # Top 10 LAEPs by total EV in latest period
    latest_laep_period = laep_agg["period"].max() if not laep_agg.empty else None
    if latest_laep_period:
        laep_latest = laep_agg[laep_agg["period"] == latest_laep_period]
        laep_totals = (
            laep_latest.groupby("LAEP", as_index=False)["ev_count"].sum().sort_values("ev_count", ascending=False)
        )
        qa_lines.append(f"Top LAEPs by total EV in {latest_laep_period}:")
        for _, row in laep_totals.head(10).iterrows():
            qa_lines.append(f"  {row['LAEP']}: {int(row['ev_count'])}")

    qa_text = "\n".join(qa_lines)
    qa_path = os.path.join(OUTPUT_DIR, "ev_qa_summary.txt")
    with open(qa_path, "w", encoding="utf-8") as f:
        f.write(qa_text)

    print("\n--- EV QA Summary ---")
    print(qa_text)


if __name__ == "__main__":
    main()

