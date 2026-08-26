#!/usr/bin/env python3
"""
LCT MCS Processing - Test Version (Apr 2025 - Mar 2026 only)
Simplified for debugging geography resolution
"""

import os
import re
import glob
import pandas as pd
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LCT_DIR = os.path.join(PROJECT_ROOT, "lct")
LOOKUPS_DIR = os.path.join(PROJECT_ROOT, "lookups")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "project", "output_processed")

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

def parse_date_to_period(date_value) -> str:
    if pd.isna(date_value):
        return None
    try:
        if isinstance(date_value, str):
            for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"]:
                try:
                    dt = datetime.strptime(str(date_value).strip(), fmt)
                    return dt.strftime("%Y-%m-01")
                except ValueError:
                    continue
        dt = pd.to_datetime(date_value)
        return dt.strftime("%Y-%m-01")
    except Exception:
        return None

print("=" * 70)
print("MCS Processing Test - Apr 2025 to Mar 2026")
print("=" * 70)

# Load lookups with explicit encoding
print("\nLoading Geography Lookups...")
pc_path = os.path.join(LOOKUPS_DIR, "Postcode-LSOA-LAD lookup.csv")
print(f"Postcode lookup: {pc_path}")
print(f"  File exists: {os.path.exists(pc_path)}")
print(f"  File size: {os.path.getsize(pc_path) / (1024*1024):.1f} MB")

try:
    # Load with explicit encoding
    pc_df = pd.read_csv(pc_path, encoding='latin-1', low_memory=False)
    print(f"  OK Loaded: {len(pc_df)} rows")
    print(f"  Columns: {pc_df.columns.tolist()[:5]}...")

    # Standardize postcode column (first column)
    pc_df['pcds_std'] = pc_df.iloc[:, 0].apply(standardize_postcode)
    pc_df['LSOA11CD'] = pc_df[pc_df.columns[7]]  # Column 7 is LSOA11CD based on od output
    pc_df = pc_df[['pcds_std', 'LSOA11CD']].drop_duplicates()
    print(f"  After cleanup: {len(pc_df)} rows")
except Exception as e:
    print(f"  ERROR: {e}")
    pc_df = pd.DataFrame()

# Load bridge lookup
bridge_path = os.path.join(LOOKUPS_DIR, "LSOA_(2011)_to_LSOA_(2021)_to_Local_Authority_District_(2022)_Exact_Fit_Lookup_for_EW_(V3).csv")
try:
    bridge_df = pd.read_csv(bridge_path)
    print(f"Bridge lookup: {len(bridge_df)} rows")
except Exception as e:
    print(f"  ✗ Error: {e}")
    bridge_df = pd.DataFrame()

# Process MCS files
print("\nProcessing MCS Files...")
mcs_dir = os.path.join(LCT_DIR, "MCS")
mcs_files = sorted(glob.glob(os.path.join(mcs_dir, "*.csv")))
print(f"Found {len(mcs_files)} MCS CSV files")

all_records = []
for filepath in mcs_files:
    filename = os.path.basename(filepath)
    try:
        df = pd.read_csv(filepath)

        # Filter by date range and heat pump type
        df['period'] = df['Commissioning Date'].apply(parse_date_to_period)

        # Filter Apr 2025 - Mar 2026
        mask_date = (df['period'] >= '2025-04-01') & (df['period'] <= '2026-03-01')

        # Filter heat pump types
        df['tech_lower'] = df['Technology Type'].str.lower()
        mask_hp = df['tech_lower'].str.contains('heat pump', na=False)

        df_filtered = df[mask_date & mask_hp].copy()

        if len(df_filtered) > 0:
            print(f"  {filename}: {len(df_filtered)} HP records")

            # Add postcode and geography
            df_filtered['postcode'] = df_filtered['Postcode'].apply(standardize_postcode)
            df_filtered['capacity_kw'] = df_filtered['Total Installed Capacity'].apply(parse_capacity_to_kw)

            # Join postcode lookup
            df_filtered = df_filtered.merge(pc_df, left_on='postcode', right_on='pcds_std', how='left')

            # Join bridge
            df_filtered = df_filtered.merge(bridge_df, left_on='LSOA11CD', right_on='LSOA11CD', how='left', suffixes=('', '_bridge'))

            all_records.append(df_filtered)
    except Exception as e:
        print(f"  {filename}: Error - {str(e)[:60]}")

if all_records:
    df_all = pd.concat(all_records, ignore_index=True)
    print(f"\nTotal records: {len(df_all)}")
    print(f"With postcode match: {df_all['LSOA11CD'].notna().sum()}")
    print(f"With LSOA21 match: {df_all['LSOA21CD'].notna().sum()}")

    # Show sample of matched and unmatched
    print(f"\nSample matched records:")
    matched = df_all[df_all['LSOA21CD'].notna()].head(2)
    if len(matched) > 0:
        print(matched[['Postcode', 'postcode', 'LSOA11CD', 'LSOA21CD']].to_string())

    print(f"\nSample unmatched records:")
    unmatched = df_all[df_all['LSOA21CD'].isna()].head(2)
    if len(unmatched) > 0:
        print(unmatched[['Postcode', 'postcode', 'LSOA11CD']].to_string())
else:
    print("No records found")

print("\n" + "=" * 70)
print("Test complete")
