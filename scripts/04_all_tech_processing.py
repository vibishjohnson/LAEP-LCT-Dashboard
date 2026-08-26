#!/usr/bin/env python3
"""
LCT Actuals - All Technology Types (MCS + LCT Register, MPAN Deduped)
Processes: Heat Pumps, Solar PV, Battery Storage, EV Chargers
"""

import os
import re
import glob
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LCT_DIR = os.path.join(PROJECT_ROOT, "lct")
INPUT_LOOKUPS = os.path.join(PROJECT_ROOT, "lookups")
OUTPUT_PROCESSED = os.path.join(PROJECT_ROOT, "project", "output_processed")

def standardize_postcode(s):
    return s.str.upper().str.replace(" ", "", regex=False).str.strip()

def parse_dates(s):
    """Parse dates - handles both ISO (YYYY-MM-DD) and UK (DD/MM/YYYY) formats"""
    result = pd.to_datetime(s, format='%Y-%m-%d', errors='coerce')
    mask_nat = result.isna()
    if mask_nat.any():
        result.loc[mask_nat] = pd.to_datetime(s[mask_nat], format='%d/%m/%Y', errors='coerce')
    return result

def get_technology_type(tech_string):
    """Classify technology into standard categories"""
    if pd.isna(tech_string):
        return None
    tech_lower = str(tech_string).lower()

    if 'heat pump' in tech_lower:
        return 'Heat Pump'
    elif any(x in tech_lower for x in ['solar pv', 'solar photovoltaic', 'solar keymark']):
        return 'Solar PV'
    elif 'battery' in tech_lower or 'storage' in tech_lower:
        return 'Battery Storage'
    elif 'ev charging' in tech_lower or 'v2g' in tech_lower:
        return 'EV Charger'
    else:
        return None

print("=" * 70)
print("LCT Actuals - All Technology Types (MCS + LCT Register)")
print("=" * 70)

# Load spatial postcode lookup
print("\nLoading Geography Lookups...")
pc_lookup = pd.read_csv(os.path.join(INPUT_LOOKUPS, "postcode_lsoa21_lookup_spatial.csv"))
pc_lookup['pcds_std'] = standardize_postcode(pc_lookup['postcode'])
pc_lookup = pc_lookup[['pcds_std', 'LSOA21CD']].drop_duplicates()
pc_lookup.set_index('pcds_std', inplace=True)
print(f"  Postcode-LSOA21 lookup: {len(pc_lookup)} rows")

# Load DNO lookup
dno_lookup = pd.read_csv(os.path.join(INPUT_LOOKUPS, "LSOA to DNO.csv"), encoding='utf-8-sig')
dno_lookup = dno_lookup[['LSOA21CD', 'LAD22CD', 'LAD22NM', 'MSOA21CD', 'MSOA21NM', 'Majority Licence area']].copy()
dno_lookup.columns = ['LSOA21CD', 'LAD22CD', 'LAD22NM', 'MSOA21CD', 'MSOA21NM', 'DNO']
dno_lookup.set_index('LSOA21CD', inplace=True)
print(f"  LSOA21->DNO lookup: {len(dno_lookup)} rows")

# ============================================================================
# PROCESS MCS (Primary source)
# ============================================================================
print("\n--- Phase 1: MCS Processing (All Technologies) ---")
mcs_dir = os.path.join(LCT_DIR, "MCS")
csv_files = sorted(glob.glob(os.path.join(mcs_dir, "*.csv")))

mcs_records = []
mcs_mpans = set()

for i, filepath in enumerate(csv_files, 1):
    filename = os.path.basename(filepath)
    try:
        df = pd.read_csv(filepath, low_memory=False)

        # Parse dates
        df['period'] = parse_dates(df['Commissioning Date']).dt.to_period('M').astype(str)

        # Filter date range (Apr 2025 - Mar 2026)
        mask_date = (df['period'] >= '2025-04') & (df['period'] <= '2026-03')
        df = df[mask_date]

        if len(df) == 0:
            continue

        # Classify technology
        df['tech_type'] = df['Technology Type'].apply(get_technology_type)
        df = df[df['tech_type'].notna()]

        if len(df) == 0:
            continue

        # Standardize postcode
        df['postcode_std'] = standardize_postcode(df['Postcode'])

        # Join postcode lookup
        df = df.join(pc_lookup, on='postcode_std', how='left')
        df = df[df['LSOA21CD'].notna()]

        if len(df) == 0:
            continue

        # Join DNO lookup
        df = df.join(dno_lookup, on='LSOA21CD', how='left')

        # Parse capacity
        def parse_kw(s):
            if pd.isna(s):
                return 0.0
            s_str = str(s).strip()
            match = re.search(r'[\d,]+\.?\d*', s_str.replace(',', ''))
            if not match:
                return 0.0
            val = float(match.group().replace(',', ''))
            if 'mw' in s_str.lower():
                return val * 1000
            if 'w' in s_str.lower() and 'kw' not in s_str.lower():
                return val / 1000
            return val

        df['capacity_kw'] = df['Total Installed Capacity'].apply(parse_kw)

        # Store MPAN values for deduplication (convert to string)
        mpans = df['MPAN'].dropna().astype(str).unique()
        mcs_mpans.update(mpans)

        # Keep records
        mcs_records.append(df[['period', 'tech_type', 'LSOA21CD', 'LAD22CD', 'MSOA21CD', 'MSOA21NM', 'DNO', 'capacity_kw']])

    except Exception as e:
        print(f"  {filename}: ERROR - {str(e)[:40]}")

print(f"MCS: {sum(len(r) for r in mcs_records)} records, {len(mcs_mpans)} unique MPANs")

if mcs_records:
    df_mcs = pd.concat(mcs_records, ignore_index=True)
else:
    df_mcs = pd.DataFrame()

# ============================================================================
# PROCESS LCT REGISTER (Secondary source, deduplicated by MPAN)
# ============================================================================
print("--- Phase 2: LCT Register (MPAN Deduped) ---")
lct_path = os.path.join(LCT_DIR, "LCT Register.csv")

try:
    df_lct = pd.read_csv(lct_path, low_memory=False)

    # Parse dates
    df_lct['period'] = parse_dates(df_lct['Installation_Date']).dt.to_period('M').astype(str)

    # Filter date range
    mask_date = (df_lct['period'] >= '2025-04') & (df_lct['period'] <= '2026-03')
    df_lct = df_lct[mask_date]

    # Classify technology
    df_lct['tech_type'] = df_lct['Type'].apply(get_technology_type)
    df_lct = df_lct[df_lct['tech_type'].notna()]

    # Deduplicate by MPAN (convert to string)
    df_lct['MPAN_str'] = df_lct['MPAN'].fillna('NO_MPAN').astype(str)
    df_lct_deduped = df_lct[~df_lct['MPAN_str'].isin(mcs_mpans)].copy()

    print(f"LCT: {len(df_lct_deduped)} records (removed {len(df_lct) - len(df_lct_deduped)} duplicates)")

    if len(df_lct_deduped) > 0:
        # Standardize postcode
        df_lct_deduped['postcode_std'] = standardize_postcode(df_lct_deduped['MPAN_Postcode'])

        # Join postcode lookup
        df_lct_deduped = df_lct_deduped.join(pc_lookup, on='postcode_std', how='left')
        df_lct_deduped = df_lct_deduped[df_lct_deduped['LSOA21CD'].notna()]

        # Join DNO lookup
        df_lct_deduped = df_lct_deduped.drop('DNO', axis=1, errors='ignore')
        df_lct_deduped = df_lct_deduped.join(dno_lookup, on='LSOA21CD', how='left')

        # Parse capacity
        def get_capacity_kw(row):
            val = row.get('Generation_Rating', 0) or row.get('Import_Rating', 0)
            if pd.isna(val) or val == 0:
                return 0.0
            return float(val)

        df_lct_deduped['capacity_kw'] = df_lct_deduped.apply(get_capacity_kw, axis=1)
        df_lct_deduped = df_lct_deduped[['period', 'tech_type', 'LSOA21CD', 'LAD22CD', 'MSOA21CD', 'MSOA21NM', 'DNO', 'capacity_kw']]

except Exception as e:
    print(f"LCT error: {e}")
    df_lct_deduped = pd.DataFrame()

# ============================================================================
# COMBINE AND OUTPUT
# ============================================================================
print("\n--- Combined Results ---")

if not df_mcs.empty and not df_lct_deduped.empty:
    df_combined = pd.concat([df_mcs, df_lct_deduped], ignore_index=True)
elif not df_mcs.empty:
    df_combined = df_mcs
else:
    df_combined = df_lct_deduped

print(f"MCS records: {len(df_mcs)}")
print(f"LCT Register records (deduped): {len(df_lct_deduped)}")
print(f"Total: {len(df_combined)}")

# Aggregate by technology, period, DNO
if not df_combined.empty:
    agg = df_combined.groupby(['period', 'tech_type', 'DNO']).agg(
        install_count=('capacity_kw', 'count'),
        total_kw=('capacity_kw', 'sum'),
    ).reset_index().sort_values(['period', 'tech_type', 'DNO'])

    # Also create LSOA-level detail
    agg_lsoa = df_combined.groupby(['period', 'tech_type', 'LSOA21CD', 'LAD22CD', 'MSOA21CD', 'MSOA21NM', 'DNO']).agg(
        install_count=('capacity_kw', 'count'),
        total_kw=('capacity_kw', 'sum'),
    ).reset_index()

    os.makedirs(OUTPUT_PROCESSED, exist_ok=True)

    # Save aggregated data
    out_path = os.path.join(OUTPUT_PROCESSED, "dashboard_data_dno.csv")
    agg.to_csv(out_path, index=False)
    print(f"\nDNO-level data: {out_path}")

    # Save LSOA-level data
    out_path_lsoa = os.path.join(OUTPUT_PROCESSED, "dashboard_data_lsoa.csv")
    agg_lsoa.to_csv(out_path_lsoa, index=False)
    print(f"LSOA-level data: {out_path_lsoa}")

    # Summary
    print(f"\nSummary by Technology:")
    for tech in sorted(agg['tech_type'].unique()):
        tech_data = agg[agg['tech_type'] == tech]
        total_installs = int(tech_data['install_count'].sum())
        total_kw = tech_data['total_kw'].sum()
        print(f"  {tech:20s}: {total_installs:6d} installs, {total_kw:10,.0f} kW ({total_kw/1000:6.1f} MW)")

print("\n" + "=" * 70)
print("Done.")
