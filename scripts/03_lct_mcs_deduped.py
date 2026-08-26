#!/usr/bin/env python3
"""
LCT Actuals - MCS + LCT Register (MPAN Deduped)
DFES Methodology: MCS primary, LCT Register secondary (exclude records already in MCS by MPAN)
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

print("=" * 70)
print("LCT Actuals - MCS + LCT Register (MPAN Deduped)")
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
print("\n--- Phase 1: MCS Processing ---")
mcs_dir = os.path.join(LCT_DIR, "MCS")
csv_files = sorted(glob.glob(os.path.join(mcs_dir, "*.csv")))
print(f"Processing {len(csv_files)} MCS files...")

mcs_records = []
mcs_mpans = set()
total_mcs_input = 0
total_mcs_kept = 0

for i, filepath in enumerate(csv_files, 1):
    filename = os.path.basename(filepath)
    try:
        df = pd.read_csv(filepath, low_memory=False)
        total_mcs_input += len(df)

        # Parse dates
        df['period'] = parse_dates(df['Commissioning Date']).dt.to_period('M').astype(str)

        # Filter date range (Apr 2025 - Mar 2026)
        mask_date = (df['period'] >= '2025-04') & (df['period'] <= '2026-03')
        df = df[mask_date]

        if len(df) == 0:
            continue

        # Filter heat pump types (DFES methodology)
        tech_lower = df['Technology Type'].str.lower()
        mask_hp = tech_lower.str.contains('heat pump', na=False)
        mask_type = (
            tech_lower.str.contains('air source', na=False) |
            tech_lower.str.contains('ground', na=False) |
            tech_lower.str.contains('water source', na=False) |
            tech_lower.str.contains('exhaust air', na=False)
        )
        df = df[mask_hp & mask_type]

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

        # Store MPAN values for deduplication (convert to string for matching)
        mpans = df['MPAN'].dropna().astype(str).unique()
        mcs_mpans.update(mpans)

        # Keep records
        mcs_records.append(df[['period', 'LSOA21CD', 'LAD22CD', 'MSOA21CD', 'MSOA21NM', 'DNO', 'capacity_kw', 'MPAN']])
        total_mcs_kept += len(df)
        print(f"  [{i:2d}] {filename}: {len(df):5d} records")

    except Exception as e:
        print(f"  [{i:2d}] {filename}: ERROR - {str(e)[:40]}")

print(f"\nMCS Summary:")
print(f"  Total input: {total_mcs_input}")
print(f"  Total kept: {total_mcs_kept}")
print(f"  Unique MPANs: {len(mcs_mpans)}")

if mcs_records:
    df_mcs = pd.concat(mcs_records, ignore_index=True)
else:
    df_mcs = pd.DataFrame()

# ============================================================================
# PROCESS LCT REGISTER (Secondary source, deduplicated by MPAN)
# ============================================================================
print("\n--- Phase 2: LCT Register (MPAN Deduped) ---")
lct_path = os.path.join(LCT_DIR, "LCT Register.csv")

try:
    df_lct = pd.read_csv(lct_path, low_memory=False)
    print(f"LCT Register loaded: {len(df_lct)} records")

    # Parse dates (using Installation_Date column)
    df_lct['period'] = parse_dates(df_lct['Installation_Date']).dt.to_period('M').astype(str)

    # Filter date range
    mask_date = (df_lct['period'] >= '2025-04') & (df_lct['period'] <= '2026-03')
    df_lct = df_lct[mask_date]
    print(f"  After date filter: {len(df_lct)}")

    # Filter heat pump types (LCT Register uses Type column)
    # Include ALL heat pump types: generic "Heat Pump" + specific types
    type_lower = df_lct['Type'].str.lower()
    mask_hp = type_lower.str.contains('heat pump', na=False)
    df_lct = df_lct[mask_hp]
    print(f"  After HP type filter (all types): {len(df_lct)}")

    # Deduplicate by MPAN - remove records already in MCS (convert to string for matching)
    df_lct['MPAN_str'] = df_lct['MPAN'].fillna('NO_MPAN').astype(str)
    df_lct_deduped = df_lct[~df_lct['MPAN_str'].isin(mcs_mpans)].copy()
    print(f"  After MPAN deduplication: {len(df_lct_deduped)} (removed {len(df_lct) - len(df_lct_deduped)})")

    if len(df_lct_deduped) > 0:
        # Standardize postcode (LCT Register uses MPAN_Postcode)
        df_lct_deduped['postcode_std'] = standardize_postcode(df_lct_deduped['MPAN_Postcode'])

        # Join postcode lookup
        df_lct_deduped = df_lct_deduped.join(pc_lookup, on='postcode_std', how='left')
        df_lct_deduped = df_lct_deduped[df_lct_deduped['LSOA21CD'].notna()]
        print(f"  After postcode match: {len(df_lct_deduped)}")

        # Join DNO lookup (drop existing DNO column first to avoid overlap)
        df_lct_deduped = df_lct_deduped.drop('DNO', axis=1, errors='ignore')
        df_lct_deduped = df_lct_deduped.join(dno_lookup, on='LSOA21CD', how='left')

        # Parse capacity (LCT Register uses Generation_Rating or Import_Rating)
        def get_capacity_kw(row):
            # Try Generation_Rating first, then Import_Rating
            val = row.get('Generation_Rating', 0) or row.get('Import_Rating', 0)
            if pd.isna(val) or val == 0:
                return 0.0
            return float(val)

        df_lct_deduped['capacity_kw'] = df_lct_deduped.apply(get_capacity_kw, axis=1)
        df_lct_deduped = df_lct_deduped[['period', 'LSOA21CD', 'LAD22CD', 'MSOA21CD', 'MSOA21NM', 'DNO', 'capacity_kw']]

except Exception as e:
    print(f"Error processing LCT Register: {e}")
    df_lct_deduped = pd.DataFrame()

# ============================================================================
# COMBINE AND OUTPUT
# ============================================================================
print("\n--- Combined Results ---")

if not df_mcs.empty and not df_lct_deduped.empty:
    # Remove MPAN column from MCS before combining
    df_mcs = df_mcs.drop('MPAN', axis=1)
    df_combined = pd.concat([df_mcs, df_lct_deduped], ignore_index=True)
elif not df_mcs.empty:
    df_mcs = df_mcs.drop('MPAN', axis=1)
    df_combined = df_mcs
else:
    df_combined = df_lct_deduped

print(f"MCS records: {len(df_mcs)}")
print(f"LCT Register records (deduped): {len(df_lct_deduped)}")
print(f"TOTAL: {len(df_combined)}")

# Aggregate by DNO and month
if not df_combined.empty:
    agg_dno = df_combined.groupby(['period', 'DNO']).agg(
        install_count=('capacity_kw', 'count'),
        total_kw=('capacity_kw', 'sum'),
    ).reset_index().sort_values(['period', 'DNO'])

    os.makedirs(OUTPUT_PROCESSED, exist_ok=True)
    out_path = os.path.join(OUTPUT_PROCESSED, "dno_lct_mcs_deduped.csv")
    agg_dno.to_csv(out_path, index=False)

    print(f"\nOutput: {out_path}")

    print(f"\nSummary by DNO:")
    for dno in sorted(agg_dno['DNO'].dropna().unique()):
        dno_data = agg_dno[agg_dno['DNO'] == dno]
        total_installs = int(dno_data['install_count'].sum())
        total_kw = dno_data['total_kw'].sum()
        print(f"  {str(dno):4s}: {total_installs:6d} installs, {total_kw:10,.0f} kW ({total_kw/1000:6.1f} MW)")

    print(f"\nOverall:")
    print(f"  Total installs: {int(agg_dno['install_count'].sum())}")
    print(f"  Total capacity: {agg_dno['total_kw'].sum():.0f} kW ({agg_dno['total_kw'].sum()/1000:.1f} MW)")

print("\n" + "=" * 70)
print("Done.")
