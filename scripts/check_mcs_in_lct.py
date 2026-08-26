#!/usr/bin/env python3
"""Check if all MCS records are captured in the LCT Register"""

import os
import pandas as pd
import re
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LCT_DIR = os.path.join(PROJECT_ROOT, "lct")

def standardize_postcode(s):
    return s.str.upper().str.replace(" ", "", regex=False).str.strip()

def parse_dates(s):
    """Parse dates - handles both ISO (YYYY-MM-DD) and UK (DD/MM/YYYY) formats"""
    result = pd.to_datetime(s, format='%Y-%m-%d', errors='coerce')
    mask_nat = result.isna()
    if mask_nat.any():
        result.loc[mask_nat] = pd.to_datetime(s[mask_nat], format='%d/%m/%Y', errors='coerce')
    return result

print("=" * 70)
print("Checking if all MCS records are in LCT Register")
print("=" * 70)

# ============================================================================
# Collect all MCS MPANs from Apr 2025-Mar 2026
# ============================================================================
print("\n--- Phase 1: Extract MCS MPANs ---")

mcs_dir = os.path.join(LCT_DIR, "MCS")
mcs_mpans = set()
mcs_records_total = 0
mcs_records_hp = 0

import glob
csv_files = sorted(glob.glob(os.path.join(mcs_dir, "*.csv")))

for filepath in csv_files:
    try:
        df = pd.read_csv(filepath, low_memory=False)
        mcs_records_total += len(df)

        # Filter by date range
        df['period'] = parse_dates(df['Commissioning Date']).dt.to_period('M').astype(str)
        mask_date = (df['period'] >= '2025-04') & (df['period'] <= '2026-03')
        df = df[mask_date]

        # Filter heat pumps
        tech_lower = df['Technology Type'].str.lower()
        mask_hp = tech_lower.str.contains('heat pump', na=False)
        mask_type = (
            tech_lower.str.contains('air source', na=False) |
            tech_lower.str.contains('ground', na=False) |
            tech_lower.str.contains('water source', na=False) |
            tech_lower.str.contains('exhaust air', na=False)
        )
        df = df[mask_hp & mask_type]
        mcs_records_hp += len(df)

        # Collect MPANs
        mpans = df['MPAN'].dropna().unique()
        mcs_mpans.update(mpans)

    except Exception as e:
        print(f"Error reading {os.path.basename(filepath)}: {e}")

print(f"MCS records found:")
print(f"  Total input records: {mcs_records_total}")
print(f"  Heat pump records (Apr 2025-Mar 2026): {mcs_records_hp}")
print(f"  Unique MPANs: {len(mcs_mpans)}")

# ============================================================================
# Load LCT Register and check for MCS MPANs
# ============================================================================
print("\n--- Phase 2: Check LCT Register ---")

lct_path = os.path.join(LCT_DIR, "LCT Register.csv")
df_lct = pd.read_csv(lct_path, low_memory=False)
print(f"LCT Register total records: {len(df_lct)}")

# Extract LCT Register MPANs (all records, any date range)
lct_mpans = set(df_lct['MPAN'].dropna().unique())
print(f"Unique MPANs in LCT Register: {len(lct_mpans)}")

# Check overlap
mcs_in_lct = mcs_mpans.intersection(lct_mpans)
mcs_not_in_lct = mcs_mpans - lct_mpans

print(f"\n--- Coverage Analysis ---")
print(f"MCS MPANs found in LCT Register: {len(mcs_in_lct)}")
print(f"MCS MPANs NOT in LCT Register: {len(mcs_not_in_lct)}")
print(f"Coverage: {len(mcs_in_lct) * 100 / len(mcs_mpans):.1f}%")

# ============================================================================
# For MCS MPANs in LCT, check if they match on Type
# ============================================================================
if len(mcs_in_lct) > 0:
    print(f"\n--- Type Matching Check ---")

    # Get MCS types
    mcs_by_mpan = {}
    for filepath in csv_files:
        try:
            df = pd.read_csv(filepath, low_memory=False)
            df['period'] = parse_dates(df['Commissioning Date']).dt.to_period('M').astype(str)
            mask_date = (df['period'] >= '2025-04') & (df['period'] <= '2026-03')
            df = df[mask_date]
            tech_lower = df['Technology Type'].str.lower()
            mask_hp = tech_lower.str.contains('heat pump', na=False)
            mask_type = (
                tech_lower.str.contains('air source', na=False) |
                tech_lower.str.contains('ground', na=False) |
                tech_lower.str.contains('water source', na=False) |
                tech_lower.str.contains('exhaust air', na=False)
            )
            df = df[mask_hp & mask_type]
            for _, row in df.iterrows():
                mpan = row['MPAN']
                if pd.notna(mpan):
                    mcs_by_mpan[mpan] = row['Technology Type']
        except:
            pass

    # Get LCT types for matching MPANs
    lct_by_mpan = {}
    for _, row in df_lct.iterrows():
        mpan = row['MPAN']
        if pd.notna(mpan) and mpan in mcs_in_lct:
            lct_by_mpan[mpan] = row['Type']

    print(f"Checking {len(mcs_in_lct)} matching MPANs...")
    type_matches = 0
    type_mismatches = []

    for mpan in list(mcs_in_lct)[:100]:  # Check first 100
        mcs_type = mcs_by_mpan.get(mpan, 'UNKNOWN')
        lct_type = lct_by_mpan.get(mpan, 'NOT_FOUND')

        mcs_lower = str(mcs_type).lower()
        lct_lower = str(lct_type).lower()

        if 'air source' in mcs_lower and 'air source' in lct_lower:
            type_matches += 1
        elif 'ground' in mcs_lower and 'ground' in lct_lower:
            type_matches += 1
        elif 'water source' in mcs_lower and 'water source' in lct_lower:
            type_matches += 1
        elif 'exhaust air' in mcs_lower and 'exhaust air' in lct_lower:
            type_matches += 1
        else:
            if len(type_mismatches) < 5:
                type_mismatches.append((mpan, mcs_type, lct_type))

    print(f"  Type match rate (sample of 100): {type_matches}/100")
    if type_mismatches:
        print(f"  Sample mismatches:")
        for mpan, mcs_t, lct_t in type_mismatches:
            print(f"    MPAN {mpan}: MCS='{mcs_t}', LCT='{lct_t}'")

print("\n" + "=" * 70)
print("Analysis complete.")
print("=" * 70)
