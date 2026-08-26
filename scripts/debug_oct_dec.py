#!/usr/bin/env python3
"""Debug why October and December have low heat pump counts"""

import os
import re
import pandas as pd
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LCT_DIR = os.path.join(PROJECT_ROOT, "lct")
INPUT_LOOKUPS = os.path.join(PROJECT_ROOT, "lookups")

def standardize_postcode(s):
    return s.str.upper().str.replace(" ", "", regex=False).str.strip()

# Load postcode lookup
pc_chunks = []
pc_path = os.path.join(INPUT_LOOKUPS, "Postcode-LSOA-LAD lookup.csv")
for chunk in pd.read_csv(pc_path, encoding='latin-1', usecols=[0, 7], chunksize=50000):
    chunk.columns = ['postcode', 'lsoa11cd']
    chunk['pcds_std'] = standardize_postcode(chunk['postcode'])
    chunk['LSOA11CD'] = chunk['lsoa11cd'].astype(str).str.strip()
    chunk = chunk[chunk['LSOA11CD'].str.startswith('E01')]
    pc_chunks.append(chunk[['pcds_std', 'LSOA11CD']])

pc_lookup = pd.concat(pc_chunks, ignore_index=True).drop_duplicates()
pc_lookup.set_index('pcds_std', inplace=True)
print(f"Postcode lookup: {len(pc_lookup)} entries")

# Load bridge
bridge_path = os.path.join(INPUT_LOOKUPS, "LSOA_(2011)_to_LSOA_(2021)_to_Local_Authority_District_(2022)_Exact_Fit_Lookup_for_EW_(V3).csv")
bridge = pd.read_csv(bridge_path)
bridge.set_index('LSOA11CD', inplace=True)
print(f"Bridge lookup: {len(bridge)} entries\n")

# Test October and December files
for filename in ["UKPN Data for October 2025.csv", "UKPN Data for December 2025.csv"]:
    print(f"=== {filename} ===")
    filepath = os.path.join(LCT_DIR, "MCS", filename)
    df = pd.read_csv(filepath)
    print(f"Total rows: {len(df)}")

    # Filter date range
    df['period'] = pd.to_datetime(df['Commissioning Date'], errors='coerce').dt.to_period('M').astype(str)
    month = filename.split()[-2].lower()
    if month == 'october':
        mask_date = df['period'] == '2025-10'
    else:
        mask_date = df['period'] == '2025-12'

    df = df[mask_date]
    print(f"After date filter: {len(df)}")

    # Filter heat pump type
    tech_lower = df['Technology Type'].str.lower()
    mask_hp = tech_lower.str.contains('heat pump', na=False)
    mask_type = (
        tech_lower.str.contains('air source', na=False) |
        tech_lower.str.contains('ground', na=False) |
        tech_lower.str.contains('water source', na=False) |
        tech_lower.str.contains('exhaust air', na=False)
    )
    df = df[mask_hp & mask_type]
    print(f"After HP type filter: {len(df)}")

    # Standardize postcode
    df['postcode_std'] = standardize_postcode(df['Postcode'])

    # Join postcode lookup
    before_pc = len(df)
    df = df.join(pc_lookup, on='postcode_std', how='left')
    df = df[df['LSOA11CD'].notna()]
    print(f"After postcode match: {len(df)} (matched {len(df)} of {before_pc})")

    if len(df) == 0:
        print("No records after postcode matching!")
        print(f"  Sample postcodes: {pd.read_csv(filepath)['Postcode'].head(3).tolist()}")
        print(f"  Standardized: {standardize_postcode(pd.read_csv(filepath)['Postcode'].head(3)).tolist()}")
        continue

    # Join bridge
    before_bridge = len(df)
    df = df.join(bridge, on='LSOA11CD', how='left')
    df = df[df['LSOA21CD'].notna()]
    print(f"After LSOA21 match: {len(df)} (matched {len(df)} of {before_bridge})")
    print()
