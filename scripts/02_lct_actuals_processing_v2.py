#!/usr/bin/env python3
"""
LCT Actuals Processing v2 - DFES Methodology
Processes: MCS + Smart Enquiries + LCT Register + ECR + ZapMap
With MPAN-based deduplication following DFES hierarchy
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

# MPAN dedup priority (lower = higher priority)
SOURCE_PRIORITY = {
    "MCS": 1,
    "Smart_Enquiries": 2,
    "LCT_Register": 3,
    "ECR_GT_1MW": 4,
    "ECR_LT_1MW": 5,
    "ZapMap": 6,
}

# Technology standardisation
TECH_MAPPING = {
    "solar pv": "Solar_PV",
    "solar photovoltaic": "Solar_PV",
    "solar thermal": "Solar_PV",
    "solar keymark": "Solar_PV",
    "ev charging point": "EVCP_Private",
    "battery storage": "Battery_Storage",
    "battery": "Battery_Storage",
    "heat pump": "Heat_Pump",
    "air source heat pump": "Heat_Pump",
    "ground/water source heat pump": "Heat_Pump",
    "ground source heat pump": "Heat_Pump",
    "water source heat pump": "Heat_Pump",
    "exhaust air heat pump": "Heat_Pump",
    "v2g - ev charging point": "Stored_Energy",
    "v2g": "Stored_Energy",
    "biomass": "Biomass",
    "wind": "Wind",
    "stored energy": "Stored_Energy",
}

def standardize_postcode(value) -> str:
    if pd.isna(value):
        return ""
    return str(value).upper().replace(" ", "").strip()

def parse_capacity_to_kw(capacity_value, source: Optional[str] = None) -> float:
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
    if source in ("ECR_GT_1MW", "ECR_LT_1MW"):
        return numeric_value * 1000
    return numeric_value

def parse_date_to_period(date_value) -> Optional[str]:
    if pd.isna(date_value):
        return None
    try:
        if isinstance(date_value, str):
            for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S",
                       "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d-%m-%Y", "%Y/%m/%d",
                       "%d.%m.%Y", "%d %b %Y", "%d %B %Y", "%Y-%m-%dT%H:%M:%S"]:
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
    for key, value in TECH_MAPPING.items():
        if key in tech_lower:
            return value
    return str(tech_name).lower().title().replace(" ", "_")

def load_geo_lookups() -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Load postcode and LSOA lookups"""
    lookup_dir = INPUT_LOOKUPS

    pc_path = os.path.join(lookup_dir, "Postcode-LSOA-LAD lookup.csv")
    if os.path.exists(pc_path):
        try:
            # Try multiple encodings
            for encoding in ['utf-8', 'latin-1', 'cp1252', 'iso-8859-1']:
                try:
                    pc_df = pd.read_csv(pc_path, encoding=encoding)
                    break
                except Exception:
                    continue
            pc_df["pcds_std"] = pc_df.iloc[:, 0].apply(standardize_postcode)
            pc_df = pc_df.rename(columns={pc_df.columns[0]: "postcode_raw"})
            print(f"Postcode-LSOA11 lookup: {len(pc_df)} rows")
        except Exception as e:
            print(f"  Error loading postcode lookup: {e}")
            pc_df = pd.DataFrame(columns=["pcds_std", "LSOA11CD"])
    else:
        pc_df = pd.DataFrame(columns=["pcds_std", "LSOA11CD"])

    bridge_path = os.path.join(lookup_dir, "LSOA_(2011)_to_LSOA_(2021)_to_Local_Authority_District_(2022)_Exact_Fit_Lookup_for_EW_(V3).csv")
    if os.path.exists(bridge_path):
        try:
            bridge_df = pd.read_csv(bridge_path)
            print(f"LSOA11-(LSOA21,LAD22) bridge: {len(bridge_df)} rows")
        except Exception:
            bridge_df = pd.DataFrame(columns=["LSOA11CD", "LSOA21CD", "LAD22CD"])
    else:
        bridge_df = pd.DataFrame(columns=["LSOA11CD", "LSOA21CD", "LAD22CD"])

    return pc_df, bridge_df

def resolve_geo(postcode, pc_lookup: pd.DataFrame, bridge_lookup: pd.DataFrame) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Resolve postcode to LSOA21CD and LAD22CD"""
    if pd.isna(postcode) or pc_lookup.empty or bridge_lookup.empty:
        return None, None, None

    std_pc = standardize_postcode(postcode)
    if not std_pc:
        return None, None, None

    try:
        pc_match = pc_lookup[pc_lookup["pcds_std"] == std_pc]
        if pc_match.empty:
            return None, None, None

        lsoa11 = str(pc_match.iloc[0]["LSOA11CD"]).strip()
        if not lsoa11:
            return None, None, None

        br = bridge_lookup[bridge_lookup["LSOA11CD"] == lsoa11]
        if br.empty:
            return None, None, lsoa11

        lsoa21 = str(br.iloc[0]["LSOA21CD"]).strip()
        lad22 = str(br.iloc[0]["LAD22CD"]).strip()
        return lsoa21 or None, lad22 or None, lsoa11
    except Exception:
        return None, None, None

def process_mcs(pc_lookup: pd.DataFrame, bridge_lookup: pd.DataFrame) -> Tuple[List[dict], Dict]:
    """Process MCS CSV files - specific heat pump types only (DFES methodology)"""
    records = []
    stats = {"ingested": 0, "dropped_no_date": 0, "dropped_no_tech": 0, "dropped_no_postcode": 0}

    mcs_dir = os.path.join(LCT_DIR, "MCS")
    if not os.path.exists(mcs_dir):
        print("  MCS directory not found")
        return records, stats

    csv_files = glob.glob(os.path.join(mcs_dir, "*.csv"))
    print(f"  Found {len(csv_files)} MCS CSV files")

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

                # Technology filtering - specific heat pump types only
                tech_raw = row.get("Technology Type")
                tech = standardise_technology(tech_raw)
                if tech != "Heat_Pump":
                    stats["dropped_no_tech"] += 1
                    continue

                # For MCS, only include specific heat pump types
                tech_raw_lower = str(tech_raw).lower() if pd.notna(tech_raw) else ""
                if not any(hp_type in tech_raw_lower for hp_type in ["air source", "ground", "water source", "exhaust air"]):
                    stats["dropped_no_tech"] += 1
                    continue

                # Capacity from Total Installed Capacity
                capacity_kw = parse_capacity_to_kw(row.get("Total Installed Capacity"))
                if capacity_kw <= 0:
                    capacity_kw = parse_capacity_to_kw(row.get("Battery Max AC Power Output"))

                # MPAN
                mpan = str(row.get("MPAN")).strip() if pd.notna(row.get("MPAN")) else None

                # Postcode
                postcode = row.get("Postcode")
                if pd.isna(postcode) and pd.isna(mpan):
                    stats["dropped_no_postcode"] += 1
                    continue

                # Resolve geography
                lsoa21, lad22, lsoa11 = resolve_geo(postcode, pc_lookup, bridge_lookup)

                records.append({
                    "period": period,
                    "technology": tech,
                    "LSOA21CD": lsoa21,
                    "LAD22CD": lad22,
                    "LSOA11CD": lsoa11,
                    "capacity_kw": capacity_kw,
                    "g99_status": None,
                    "ev_speed": None,
                    "source": "MCS",
                    "mpan": mpan,
                    "postcode_raw": postcode,
                })
                stats["ingested"] += 1

        except Exception as e:
            print(f"  Error reading {os.path.basename(filepath)}: {e}")

    return records, stats

def process_smart_enquiries(pc_lookup: pd.DataFrame, bridge_lookup: pd.DataFrame, mcs_mpans: set) -> Tuple[List[dict], Dict]:
    """Process Smart Enquiries (Device-Report) - deduplicated against MCS by MPAN"""
    records = []
    stats = {"ingested": 0, "dropped_no_date": 0, "dropped_no_tech": 0, "dropped_duplicate_mpan": 0}

    filepath = os.path.join(LCT_DIR, "Device-Report (2).csv")
    if not os.path.exists(filepath):
        print("  Smart Enquiries file not found")
        return records, stats

    try:
        df = pd.read_csv(filepath)

        for _, row in df.iterrows():
            # Parse date
            period = parse_date_to_period(row.get("Application Date Created"))
            if not period:
                stats["dropped_no_date"] += 1
                continue

            # Filter by date range (Apr 2025 - Mar 2026)
            if not (period >= "2025-04-01" and period <= "2026-03-01"):
                continue

            # Technology filtering - generic "Heat Pump"
            tech_raw = row.get("Technology Type")
            tech = standardise_technology(tech_raw)
            if tech != "Heat_Pump":
                stats["dropped_no_tech"] += 1
                continue

            # MPAN - dedup against MCS
            mpan = str(row.get("MPAN")).strip() if pd.notna(row.get("MPAN")) else None
            if mpan and mpan in mcs_mpans:
                stats["dropped_duplicate_mpan"] += 1
                continue

            # Capacity from Maximum Current Demand
            capacity_kw = parse_capacity_to_kw(row.get("Application Export kW"))
            if capacity_kw <= 0:
                capacity_kw = parse_capacity_to_kw(row.get("Application Import kW"))

            # Postcode
            postcode = row.get("Postcode")

            # Resolve geography
            lsoa21, lad22, lsoa11 = resolve_geo(postcode, pc_lookup, bridge_lookup)

            records.append({
                "period": period,
                "technology": tech,
                "LSOA21CD": lsoa21,
                "LAD22CD": lad22,
                "LSOA11CD": lsoa11,
                "capacity_kw": capacity_kw,
                "g99_status": None,
                "ev_speed": None,
                "source": "Smart_Enquiries",
                "mpan": mpan,
                "postcode_raw": postcode,
            })
            stats["ingested"] += 1

    except Exception as e:
        print(f"  Error processing Smart Enquiries: {e}")

    return records, stats

def process_lct_register(pc_lookup: pd.DataFrame, bridge_lookup: pd.DataFrame, mcs_mpans: set, se_mpans: set) -> Tuple[List[dict], Dict]:
    """Process LCT Register - deduplicated against MCS and Smart Enquiries by MPAN"""
    records = []
    stats = {"ingested": 0, "dropped_no_date": 0, "dropped_no_tech": 0, "dropped_duplicate_mpan": 0}

    filepath = os.path.join(LCT_DIR, "LCT Register.csv")
    if not os.path.exists(filepath):
        print("  LCT Register file not found")
        return records, stats

    try:
        df = pd.read_csv(filepath)

        for _, row in df.iterrows():
            # Parse date
            period = parse_date_to_period(row.get("Installation_Date"))
            if not period:
                period = parse_date_to_period(row.get("Commissioning_Date"))
            if not period:
                stats["dropped_no_date"] += 1
                continue

            # Filter by date range (Apr 2025 - Mar 2026)
            if not (period >= "2025-04-01" and period <= "2026-03-01"):
                continue

            # Technology filtering - generic "Heat Pump"
            tech_raw = row.get("Type")
            tech = standardise_technology(tech_raw)
            if tech != "Heat_Pump":
                stats["dropped_no_tech"] += 1
                continue

            # MPAN - dedup against MCS and Smart Enquiries
            mpan = str(row.get("MPAN")).strip() if pd.notna(row.get("MPAN")) else None
            if mpan and (mpan in mcs_mpans or mpan in se_mpans):
                stats["dropped_duplicate_mpan"] += 1
                continue

            # Capacity
            capacity_kw = parse_capacity_to_kw(row.get("Generation_Rating"))
            if capacity_kw <= 0:
                capacity_kw = parse_capacity_to_kw(row.get("Import_Rating"))

            # Postcode
            postcode = row.get("MPAN_Postcode")

            # Resolve geography
            lsoa21, lad22, lsoa11 = resolve_geo(postcode, pc_lookup, bridge_lookup)

            records.append({
                "period": period,
                "technology": tech,
                "LSOA21CD": lsoa21,
                "LAD22CD": lad22,
                "LSOA11CD": lsoa11,
                "capacity_kw": capacity_kw,
                "g99_status": None,
                "ev_speed": None,
                "source": "LCT_Register",
                "mpan": mpan,
                "postcode_raw": postcode,
            })
            stats["ingested"] += 1

    except Exception as e:
        print(f"  Error processing LCT Register: {e}")

    return records, stats

def main():
    print("=" * 70)
    print("LCT Actuals Processing v2 - DFES Methodology")
    print("=" * 70)

    pc_lookup, bridge_lookup = load_geo_lookups()

    all_records = []
    all_stats = {}

    # 1. Process MCS first (primary source for heat pumps)
    print("\n--- MCS (Apr 2025 - Mar 2026, specific HP types) ---")
    mcs_records, mcs_stats = process_mcs(pc_lookup, bridge_lookup)
    mcs_mpans = set(r.get("mpan") for r in mcs_records if r.get("mpan"))
    all_records.extend(mcs_records)
    all_stats["MCS"] = mcs_stats
    print(f"  Ingested: {mcs_stats['ingested']}")
    for k, v in mcs_stats.items():
        if k != "ingested" and v > 0:
            print(f"  Dropped ({k}): {v}")

    # 2. Process Smart Enquiries (deduplicate against MCS)
    print("\n--- Smart Enquiries (Apr 2025 - Mar 2026, deduplicated vs MCS) ---")
    se_records, se_stats = process_smart_enquiries(pc_lookup, bridge_lookup, mcs_mpans)
    se_mpans = set(r.get("mpan") for r in se_records if r.get("mpan"))
    all_records.extend(se_records)
    all_stats["Smart_Enquiries"] = se_stats
    print(f"  Ingested: {se_stats['ingested']}")
    for k, v in se_stats.items():
        if k != "ingested" and v > 0:
            print(f"  Dropped ({k}): {v}")

    # 3. Process LCT Register (deduplicate against MCS + Smart Enquiries)
    print("\n--- LCT Register (Apr 2025 - Mar 2026, deduplicated vs MCS + SE) ---")
    lctr_records, lctr_stats = process_lct_register(pc_lookup, bridge_lookup, mcs_mpans, se_mpans)
    all_records.extend(lctr_records)
    all_stats["LCT_Register"] = lctr_stats
    print(f"  Ingested: {lctr_stats['ingested']}")
    for k, v in lctr_stats.items():
        if k != "ingested" and v > 0:
            print(f"  Dropped ({k}): {v}")

    # Summary
    print("\n--- Ingestion Summary (Apr 2025 - Mar 2026) ---")
    total_ingested = 0
    for src, st in all_stats.items():
        count = st.get("ingested", 0)
        total_ingested += count
        print(f"  {src}: {count} records")

    print(f"\n  TOTAL HEAT PUMPS INGESTED: {total_ingested}")

    # Filter to records with valid geography (or output all if no geography)
    with_lsoa = [r for r in all_records if r.get("LSOA21CD")]
    print(f"  Records with valid LSOA: {len(with_lsoa)}")
    print(f"  Records without LSOA (no geography resolution): {len(all_records) - len(with_lsoa)}")

    # Aggregate - include all records for summary
    if all_records:
        df = pd.DataFrame(all_records)
        if with_lsoa:
            # Aggregate by geography if available
            agg = df[df["LSOA21CD"].notna()].groupby(["period", "technology", "LSOA21CD", "LAD22CD"]).agg(
                install_count=("capacity_kw", "count"),
                total_kw=("capacity_kw", "sum"),
            ).reset_index()
        else:
            # Summary aggregation without geography
            agg = df.groupby(["period", "technology", "source"]).agg(
                install_count=("capacity_kw", "count"),
                total_kw=("capacity_kw", "sum"),
            ).reset_index()
            print(f"\n  Aggregation by Period/Technology/Source (no geography available)")
    else:
        agg = pd.DataFrame(columns=["period", "technology", "install_count", "total_kw"])

    # Output
    os.makedirs(OUTPUT_PROCESSED, exist_ok=True)
    out_path = os.path.join(OUTPUT_PROCESSED, "lsoa_lct_actuals_v2.csv")
    agg.to_csv(out_path, index=False)
    print(f"\n  Written: {out_path} ({len(agg)} rows)")

    print("\nDone.")

if __name__ == "__main__":
    main()
