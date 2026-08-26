#!/usr/bin/env python3
"""
Aggregate LSOA-level LCT actuals to LAEP and DNO licence area levels.

Inputs (run from project root):
  - project/output_processed/lsoa_lct_actuals.csv
  - lookups/LA-LAEP lookup.csv  (ID_CODE = LAD22CD, LAEP, Informed DFES)
  - ev/LSOA mapping.xlsx        (LSOA21CD → Majority Licence area: EPN/LPN/SPN)

Outputs:
  - project/output_processed/laep_lct_actuals.csv
      All LAEPs with any mapping in the lookup.
  - project/output_processed/laep_available_lct_actuals.csv
      Only LAEPs where Informed DFES = Yes (confirmed geographic boundaries).
  - project/output_processed/dno_lct_actuals.csv
      Aggregated to DNO licence area level (EPN, LPN, SPN).
"""

import os
import warnings
from typing import Tuple
from datetime import datetime

import pandas as pd

warnings.filterwarnings("ignore")


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_PROCESSED = os.path.join(PROJECT_ROOT, "project", "output_processed")
LOOKUPS_DIR = os.path.join(PROJECT_ROOT, "lookups")


def load_lsoa_actuals() -> pd.DataFrame:
    """Load LSOA-level actuals produced by 02_lct_actuals_processing.py."""
    path = os.path.join(OUTPUT_PROCESSED, "lsoa_lct_actuals.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Could not find LSOA actuals at '{path}'. "
            "Run 'python scripts/02_lct_actuals_processing.py' first."
        )
    df = pd.read_csv(path)
    expected = {"period", "technology", "LSOA21CD", "LAD22CD", "install_count", "total_kw"}
    missing = expected - set(df.columns)
    if missing:
        raise ValueError(f"lsoa_lct_actuals.csv missing columns: {sorted(missing)}")
    if "g99_status" not in df.columns:
        df["g99_status"] = None
    if "ev_speed" not in df.columns:
        df["ev_speed"] = None
    return df


def load_laep_lookup() -> pd.DataFrame:
    """
    Load LA to LAEP mapping.

    Returns df with columns: LAD22CD, LAEP, Informed_DFES.
    Informed_DFES is 'Yes' for LAEPs with confirmed geographic boundaries.
    """
    path = os.path.join(LOOKUPS_DIR, "LA-LAEP lookup.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Could not find LA-LAEP lookup at '{path}'.")

    df_raw = None
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            df_raw = pd.read_csv(path, encoding=enc)
            break
        except Exception:
            continue
    if df_raw is None:
        raise RuntimeError("Failed to read 'LA-LAEP lookup.csv' with common encodings.")

    id_col = laep_col = dfes_col = None
    for c in df_raw.columns:
        lc = c.lower()
        if lc == "id_code":
            id_col = c
        elif lc == "laep":
            laep_col = c
        elif "informed" in lc and "dfes" in lc:
            dfes_col = c

    if id_col is None or laep_col is None:
        raise ValueError("LA-LAEP lookup must contain 'ID_CODE' and 'LAEP' columns.")

    cols = [id_col, laep_col] + ([dfes_col] if dfes_col else [])
    df = df_raw[cols].drop_duplicates()
    rename = {id_col: "LAD22CD", laep_col: "LAEP"}
    if dfes_col:
        rename[dfes_col] = "Informed_DFES"
    df = df.rename(columns=rename)
    if "Informed_DFES" not in df.columns:
        df["Informed_DFES"] = None

    df["LAD22CD"] = df["LAD22CD"].astype(str).str.strip()
    df["LAEP"] = df["LAEP"].astype(str).str.strip()
    df["LAEP"] = df["LAEP"].replace("nan", None)
    return df


def load_dno_lookup() -> pd.DataFrame:
    """
    Load LSOA21CD → DNO licence area (EPN/LPN/SPN) from ev/LSOA mapping.xlsx.

    Uses the 'Majority Licence area' column to assign each LSOA to a single DNO.
    """
    path = os.path.join(PROJECT_ROOT, "ev", "LSOA mapping.xlsx")
    if not os.path.exists(path):
        print(f"  Warning: LSOA mapping not found at '{path}' — DNO aggregation will be skipped.")
        return pd.DataFrame(columns=["LSOA21CD", "DNO"])

    try:
        df = pd.read_excel(path)
    except Exception as e:
        print(f"  Warning: Failed to read LSOA mapping: {e} — DNO aggregation will be skipped.")
        return pd.DataFrame(columns=["LSOA21CD", "DNO"])

    lsoa_col = dno_col = None
    for c in df.columns:
        lc = str(c).lower()
        if lc == "lsoa21cd":
            lsoa_col = c
        elif "majority" in lc and "licence" in lc:
            dno_col = c

    if lsoa_col is None or dno_col is None:
        print("  Warning: LSOA mapping missing LSOA21CD or 'Majority Licence area' column — DNO aggregation will be skipped.")
        return pd.DataFrame(columns=["LSOA21CD", "DNO"])

    out = df[[lsoa_col, dno_col]].drop_duplicates(subset=[lsoa_col]).copy()
    out = out.rename(columns={lsoa_col: "LSOA21CD", dno_col: "DNO"})
    out["LSOA21CD"] = out["LSOA21CD"].astype(str).str.strip()
    out["DNO"] = out["DNO"].astype(str).str.strip()
    out = out[out["DNO"].notna() & (out["DNO"] != "nan") & (out["DNO"] != "")]
    print(f"LSOA21CD-DNO lookup: {len(out)} rows, DNO areas: {sorted(out['DNO'].unique().tolist())}")
    return out


def aggregate_to_laep(lsoa_df: pd.DataFrame, laep_df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """
    Join LAD22CD to LAEP and aggregate to LAEP-month-tech level.

    laep_df must have columns LAD22CD and LAEP.
    Returns (aggregated_df, count_of_lsoa_rows_with_no_laep_match).
    """
    merged = lsoa_df.merge(laep_df[["LAD22CD", "LAEP"]], on="LAD22CD", how="left")
    missing_laep = int(merged["LAEP"].isna().sum())
    merged = merged[merged["LAEP"].notna()].copy()

    agg = (
        merged.groupby(["period", "technology", "LAEP", "g99_status", "ev_speed"], dropna=False)
        .agg(
            install_count=("install_count", "sum"),
            total_kw=("total_kw", "sum"),
        )
        .reset_index()
    )
    return agg, missing_laep


def aggregate_to_dno(lsoa_df: pd.DataFrame, dno_df: pd.DataFrame) -> Tuple[pd.DataFrame, int]:
    """
    Join LSOA21CD to DNO licence area and aggregate to DNO-month-tech level.

    Returns (aggregated_df, count_of_lsoa_rows_with_no_dno_match).
    """
    if dno_df.empty:
        return pd.DataFrame(columns=["period", "technology", "DNO", "g99_status", "ev_speed", "install_count", "total_kw"]), 0

    merged = lsoa_df.merge(dno_df, on="LSOA21CD", how="left")
    missing_dno = int(merged["DNO"].isna().sum())
    merged = merged[merged["DNO"].notna()].copy()

    agg = (
        merged.groupby(["period", "technology", "DNO", "g99_status", "ev_speed"], dropna=False)
        .agg(
            install_count=("install_count", "sum"),
            total_kw=("total_kw", "sum"),
        )
        .reset_index()
    )
    return agg, missing_dno


def main():
    print("=" * 60)
    print("LCT Actuals – aggregate to LAEP and DNO levels")
    print("=" * 60)

    lsoa_df = load_lsoa_actuals()
    laep_lookup = load_laep_lookup()
    dno_df = load_dno_lookup()

    print(f"Loaded LSOA actuals: {len(lsoa_df)} rows")
    print(f"Loaded LA-LAEP lookup: {len(laep_lookup)} rows")

    os.makedirs(OUTPUT_PROCESSED, exist_ok=True)

    # ------------------------------------------------------------------
    # LAEP aggregation – all mapped LAEPs
    # ------------------------------------------------------------------
    laep_full = laep_lookup[laep_lookup["LAEP"].notna()][["LAD22CD", "LAEP"]].copy()
    agg_laep, missing_laep = aggregate_to_laep(lsoa_df, laep_full)

    out_laep = os.path.join(OUTPUT_PROCESSED, "laep_lct_actuals.csv")
    try:
        agg_laep.to_csv(out_laep, index=False)
    except PermissionError:
        # If file is locked, save with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_laep = os.path.join(OUTPUT_PROCESSED, f"laep_lct_actuals_{timestamp}.csv")
        agg_laep.to_csv(out_laep, index=False)
        print(f"File was locked, saved as: {out_laep}")

    print("\n--- LAEP aggregation (all) ---")
    print(f"  Output rows: {len(agg_laep)}")
    if not agg_laep.empty:
        print(f"  Date range: {agg_laep['period'].min()} to {agg_laep['period'].max()}")
        print(f"  Technologies: {sorted(agg_laep['technology'].unique().tolist())}")
        print(f"  LAEPs: {sorted(agg_laep['LAEP'].unique().tolist())}")
        print(f"  Total installs: {int(agg_laep['install_count'].sum())}")
        print(f"  Total kW: {agg_laep['total_kw'].sum():.2f}")
    print(f"  LSOA rows with no LAEP match: {missing_laep}")
    print(f"  Written: {out_laep}")

    # ------------------------------------------------------------------
    # LAEP aggregation – available boundaries only (Informed DFES = Yes)
    # ------------------------------------------------------------------
    laep_available = laep_lookup[
        laep_lookup["Informed_DFES"].astype(str).str.strip().str.lower() == "yes"
    ][["LAD22CD", "LAEP"]].copy()
    agg_laep_avail, missing_laep_avail = aggregate_to_laep(lsoa_df, laep_available)

    out_laep_avail = os.path.join(OUTPUT_PROCESSED, "laep_available_lct_actuals.csv")
    try:
        agg_laep_avail.to_csv(out_laep_avail, index=False)
    except PermissionError:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_laep_avail = os.path.join(OUTPUT_PROCESSED, f"laep_available_lct_actuals_{timestamp}.csv")
        agg_laep_avail.to_csv(out_laep_avail, index=False)
        print(f"File was locked, saved as: {out_laep_avail}")

    print("\n--- LAEP aggregation (available boundaries, Informed DFES = Yes) ---")
    print(f"  Output rows: {len(agg_laep_avail)}")
    if not agg_laep_avail.empty:
        print(f"  LAEPs: {sorted(agg_laep_avail['LAEP'].unique().tolist())}")
        print(f"  Total installs: {int(agg_laep_avail['install_count'].sum())}")
        print(f"  Total kW: {agg_laep_avail['total_kw'].sum():.2f}")
    print(f"  LSOA rows with no match: {missing_laep_avail}")
    print(f"  Written: {out_laep_avail}")

    # ------------------------------------------------------------------
    # DNO licence area aggregation (EPN / LPN / SPN)
    # ------------------------------------------------------------------
    agg_dno, missing_dno = aggregate_to_dno(lsoa_df, dno_df)

    out_dno = os.path.join(OUTPUT_PROCESSED, "dno_lct_actuals.csv")
    try:
        agg_dno.to_csv(out_dno, index=False)
    except PermissionError:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dno = os.path.join(OUTPUT_PROCESSED, f"dno_lct_actuals_{timestamp}.csv")
        agg_dno.to_csv(out_dno, index=False)
        print(f"File was locked, saved as: {out_dno}")

    print("\n--- DNO licence area aggregation ---")
    print(f"  Output rows: {len(agg_dno)}")
    if not agg_dno.empty:
        print(f"  Date range: {agg_dno['period'].min()} to {agg_dno['period'].max()}")
        print(f"  Technologies: {sorted(agg_dno['technology'].unique().tolist())}")
        print(f"  DNO areas: {sorted(agg_dno['DNO'].unique().tolist())}")
        print(f"  Total installs: {int(agg_dno['install_count'].sum())}")
        print(f"  Total kW: {agg_dno['total_kw'].sum():.2f}")
    print(f"  LSOA rows with no DNO match: {missing_dno}")
    print(f"  Written: {out_dno}")

    print("\nDone.")


if __name__ == "__main__":
    main()
