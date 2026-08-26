#!/usr/bin/env python3
"""Debug why October heat pumps are being filtered out"""

import os
import pandas as pd
import re

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOOKUPS_DIR = os.path.join(PROJECT_ROOT, "lookups")
MCS_DIR = os.path.join(PROJECT_ROOT, "lct", "MCS")

def standardize_postcode(s):
    return s.str.upper().str.replace(" ", "", regex=False).str.strip()

# Load spatial lookup
pc_lookup = pd.read_csv(os.path.join(LOOKUPS_DIR, "postcode_lsoa21_lookup_spatial.csv"))
pc_lookup['pcds_std'] = standardize_postcode(pc_lookup['postcode'])
pc_set = set(pc_lookup['pcds_std'].unique())
print(f"Postcode lookup: {len(pc_set)} unique postcodes\n")

# Load DNO lookup
dno_lookup = pd.read_csv(os.path.join(LOOKUPS_DIR, "LSOA to DNO.csv"), encoding='utf-8-sig')
lsoa21_set = set(dno_lookup['LSOA21CD'].dropna().unique())
print(f"DNO lookup: {len(lsoa21_set)} unique LSOA21CD\n")

# Test April vs October
for month_file in ["UKPN Data for April 2025.csv", "UKPN Data for October 2025.csv"]:
    print(f"=== {month_file} ===")
    df = pd.read_csv(os.path.join(MCS_DIR, month_file))

    # Filter heat pumps
    tech_lower = df['Technology Type'].str.lower()
    mask_hp = tech_lower.str.contains('heat pump', na=False)
    mask_type = (
        tech_lower.str.contains('air source', na=False) |
        tech_lower.str.contains('ground', na=False) |
        tech_lower.str.contains('water source', na=False) |
        tech_lower.str.contains('exhaust air', na=False)
    )
    df = df[mask_hp & mask_type].copy()
    print(f"Heat pump records: {len(df)}")

    # Standardize postcode
    df['postcode_std'] = standardize_postcode(df['Postcode'])

    # Check postcode matches
    df['pc_in_lookup'] = df['postcode_std'].isin(pc_set)
    pc_matched = df['pc_in_lookup'].sum()
    print(f"  Postcodes in lookup: {pc_matched} ({pc_matched*100/len(df):.1f}%)")
    print(f"  Postcodes NOT in lookup: {len(df) - pc_matched}")

    # Show sample of matched and unmatched
    if pc_matched > 0:
        print(f"  Sample matched postcodes: {df[df['pc_in_lookup']]['postcode_std'].head(3).tolist()}")
    unmatched = df[~df['pc_in_lookup']]
    if len(unmatched) > 0:
        print(f"  Sample unmatched postcodes: {unmatched['postcode_std'].head(3).tolist()}")

    # For matched records, check LSOA21 lookup
    if pc_matched > 0:
        df_matched = df[df['pc_in_lookup']].copy()
        df_matched = df_matched.merge(pc_lookup[['pcds_std', 'LSOA21CD']],
                                       left_on='postcode_std', right_on='pcds_std', how='left')
        df_matched['lsoa_in_dno'] = df_matched['LSOA21CD'].isin(lsoa21_set)
        lsoa_matched = df_matched['lsoa_in_dno'].sum()
        print(f"  LSOA21 in DNO lookup: {lsoa_matched} ({lsoa_matched*100/len(df_matched):.1f}%)")

    print()
