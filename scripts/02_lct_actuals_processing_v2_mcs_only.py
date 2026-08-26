#!/usr/bin/env python3
"""
LCT Actuals Processing v2 - MCS Only (England, Apr 2025 - Mar 2026)
Following DFES Methodology: Specific heat pump types with MPAN deduplication
"""

import os
import re
import glob
import warnings
from datetime import datetime
from typing import Optional, List, Dict, Tuple

import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LCT_DIR = os.path.join(PROJECT_ROOT, "lct")
INPUT_LOOKUPS = os.path.join(PROJECT_ROOT, "lookups")
OUTPUT_PROCESSED = os.path.join(PROJECT_ROOT, "project", "output_processed")

def standardize_postcode(value) -> str:
    if pd.isna(value):
        return ""
    return str(value).upper().replace(" ", "").strip()

def parse_capacity_to_kw(capacity_value) -> float:
    if pd.isna(capacity_value):
        return 0.0
    capacity_str = str(capacity_value).strip()
    numeric_match = re.search(r"[\d,]+\.?\d*", capacity_str.replace(",", ""))
    if not numeric_match:
        return 0.0
    numeric_value = float(numeric_match.group().replace(",", ""))
    capacity_lower = capacity_str.lower()
    if "mw" in capacity_lower:
        return numeric_value * 1000
    if "w" in capacity_lower and "kw" not in capacity_lower:
        return numeric_value / 1000
    return numeric_value

def parse_date_to_period(date_value) -> Optional[str]:
    if pd.isna(date_value):
        return None
    try:
        if isinstance(date_value, str):
            for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S",
                       "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d-%m-%Y", "%Y/%m/%d"]:
                try:
                    dt = datetime.strptime(str(date_value).strip(), fmt)
                    return dt.strftime("%Y-%m-01")
                except ValueError:
                    continue
        dt = pd.to_datetime(date_value)
        return dt.to_period("M").to_timestamp().strftime("%Y-%m-01")
    except Exception:
        return None

def standardise_technology(tech_name: str) -> Optional[str]:
    if pd.isna(tech_name):
        return None
    tech_lower = str(tech_name).lower().strip()
    if "heat pump" in tech_lower:
        return "Heat_Pump"
    return None

def load_geo_lookups() -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Load postcode and LSOA lookups (England only)"""
    print("Loading Geography Lookups...")

    # Load postcode lookup with appropriate encoding - use chunks for speed
    pc_path = os.path.join(INPUT_LOOKUPS, "Postcode-LSOA-LAD lookup.csv")
    try:
        print(f"  Reading {pc_path}...")
        pc_chunks = []
        for chunk in pd.read_csv(pc_path, encoding='latin-1', usecols=[0, 7], chunksize=50000):
            chunk.columns = ['postcode', 'lsoa11cd']
            chunk['pcds_std'] = chunk['postcode'].apply(standardize_postcode)
            chunk['LSOA11CD'] = chunk['lsoa11cd'].astype(str).str.strip()
            # Filter for England only during read
            chunk = chunk[chunk['LSOA11CD'].str.startswith('E01')]
            pc_chunks.append(chunk[['pcds_std', 'LSOA11CD']])

        pc_df = pd.concat(pc_chunks, ignore_index=True) if pc_chunks else pd.DataFrame(columns=["pcds_std", "LSOA11CD"])
        pc_df = pc_df.drop_duplicates()
        print(f"  Postcode-LSOA11 lookup: {len(pc_df)} rows (England only)")
    except Exception as e:
        print(f"  Error loading postcode lookup: {e}")
        pc_df = pd.DataFrame(columns=["pcds_std", "LSOA11CD"])

    # Load bridge lookup
    bridge_path = os.path.join(INPUT_LOOKUPS, "LSOA_(2011)_to_LSOA_(2021)_to_Local_Authority_District_(2022)_Exact_Fit_Lookup_for_EW_(V3).csv")
    try:
        bridge_df = pd.read_csv(bridge_path)
        print(f"  LSOA11-(LSOA21,LAD22) bridge: {len(bridge_df)} rows")
    except Exception as e:
        print(f"  Error loading bridge lookup: {e}")
        bridge_df = pd.DataFrame(columns=["LSOA11CD", "LSOA21CD", "LAD22CD"])

    return pc_df, bridge_df

def process_mcs(pc_lookup: pd.DataFrame, bridge_lookup: pd.DataFrame) -> Tuple[List[dict], Dict]:
    """Process MCS CSV files - specific heat pump types only (DFES methodology)"""
    records = []
    stats = {"ingested": 0, "dropped_no_date": 0, "dropped_no_tech": 0, "dropped_no_geography": 0, "dropped_scotland": 0}

    mcs_dir = os.path.join(LCT_DIR, "MCS")
    csv_files = sorted(glob.glob(os.path.join(mcs_dir, "*.csv")))
    print(f"\nProcessing {len(csv_files)} MCS CSV files (Apr 2025 - Mar 2026)...")

    for filepath in csv_files:
        try:
            df = pd.read_csv(filepath)
            filename = os.path.basename(filepath)

            for _, row in df.iterrows():
                # Parse date
                period = parse_date_to_period(row.get("Commissioning Date"))
                if not period:
                    stats["dropped_no_date"] += 1
                    continue

                # Filter by date range (Apr 2025 - Mar 2026)
                if not (period >= "2025-04-01" and period <= "2026-03-01"):
                    continue

                # Technology filtering - heat pump types only
                tech_raw = row.get("Technology Type")
                tech = standardise_technology(tech_raw)
                if tech != "Heat_Pump":
                    stats["dropped_no_tech"] += 1
                    continue

                # For MCS, only include specific heat pump types per DFES methodology
                tech_raw_lower = str(tech_raw).lower() if pd.notna(tech_raw) else ""
                if not any(hp_type in tech_raw_lower for hp_type in ["air source", "ground", "water source", "exhaust air"]):
                    stats["dropped_no_tech"] += 1
                    continue

                # Capacity
                capacity_kw = parse_capacity_to_kw(row.get("Total Installed Capacity"))
                if capacity_kw <= 0:
                    capacity_kw = parse_capacity_to_kw(row.get("Battery Max AC Power Output"))

                # MPAN
                mpan = str(row.get("MPAN")).strip() if pd.notna(row.get("MPAN")) else None

                # Postcode and geography
                postcode = row.get("Postcode")
                postcode_std = standardize_postcode(postcode)

                # Match to LSOA11
                if not pc_lookup.empty and postcode_std:
                    pc_match = pc_lookup[pc_lookup["pcds_std"] == postcode_std]
                    if not pc_match.empty:
                        lsoa11 = str(pc_match.iloc[0]["LSOA11CD"]).strip()

                        # Filter out Scotland
                        if not lsoa11.startswith("E01"):
                            stats["dropped_scotland"] += 1
                            continue

                        # Match to LSOA21
                        if not bridge_lookup.empty:
                            br = bridge_lookup[bridge_lookup["LSOA11CD"] == lsoa11]
                            if not br.empty:
                                lsoa21 = str(br.iloc[0]["LSOA21CD"]).strip()
                                lad22 = str(br.iloc[0]["LAD22CD"]).strip()
                            else:
                                lsoa21 = None
                                lad22 = None
                        else:
                            lsoa21 = None
                            lad22 = None
                    else:
                        stats["dropped_no_geography"] += 1
                        continue
                else:
                    stats["dropped_no_geography"] += 1
                    continue

                # Skip if no LSOA21
                if not lsoa21:
                    stats["dropped_no_geography"] += 1
                    continue

                records.append({
                    "period": period,
                    "technology": tech,
                    "LSOA21CD": lsoa21,
                    "LAD22CD": lad22,
                    "capacity_kw": capacity_kw,
                    "source": "MCS",
                    "mpan": mpan,
                })
                stats["ingested"] += 1

        except Exception as e:
            print(f"  Error reading {os.path.basename(filepath)}: {e}")

    return records, stats

def main():
    print("=" * 70)
    print("LCT Actuals Processing v2 - MCS Only (England, Apr 2025 - Mar 2026)")
    print("DFES Methodology: Specific heat pump types with LSOA21 geography")
    print("=" * 70)

    pc_lookup, bridge_lookup = load_geo_lookups()

    # Process MCS
    print("\n--- MCS Processing ---")
    mcs_records, mcs_stats = process_mcs(pc_lookup, bridge_lookup)

    print(f"\nMCS Summary:")
    print(f"  Ingested: {mcs_stats['ingested']}")
    for k, v in mcs_stats.items():
        if k != "ingested" and v > 0:
            print(f"  Dropped ({k}): {v}")

    print(f"\n  TOTAL HEAT PUMPS (England, Apr 2025 - Mar 2026): {mcs_stats['ingested']}")

    # Aggregate
    if mcs_records:
        df = pd.DataFrame(mcs_records)
        agg = df.groupby(["period", "technology", "LSOA21CD", "LAD22CD"]).agg(
            install_count=("capacity_kw", "count"),
            total_kw=("capacity_kw", "sum"),
        ).reset_index()
    else:
        agg = pd.DataFrame(columns=["period", "technology", "LSOA21CD", "LAD22CD", "install_count", "total_kw"])

    # Output
    os.makedirs(OUTPUT_PROCESSED, exist_ok=True)
    out_path = os.path.join(OUTPUT_PROCESSED, "lsoa_lct_actuals_mcs_v2.csv")
    agg.to_csv(out_path, index=False)
    print(f"\nOutput: {out_path}")
    print(f"  Rows: {len(agg)}")
    if not agg.empty:
        print(f"  Date range: {agg['period'].min()} to {agg['period'].max()}")
        print(f"  Total installs: {int(agg['install_count'].sum())}")
        print(f"  Total capacity: {agg['total_kw'].sum():.0f} kW ({agg['total_kw'].sum()/1000:.1f} MW)")

    print("\n" + "=" * 70)
    print("Done.")

if __name__ == "__main__":
    main()
