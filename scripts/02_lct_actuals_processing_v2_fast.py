#!/usr/bin/env python3
"""
LCT Actuals Processing v2 - Fast Vectorized (England, Apr 2025 - Mar 2026)
DFES Methodology with vectorized operations for speed
"""

import os
import re
import glob
import warnings
from datetime import datetime

import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LCT_DIR = os.path.join(PROJECT_ROOT, "lct")
INPUT_LOOKUPS = os.path.join(PROJECT_ROOT, "lookups")
OUTPUT_PROCESSED = os.path.join(PROJECT_ROOT, "project", "output_processed")

def standardize_postcode(s):
    return s.str.upper().str.replace(" ", "", regex=False).str.strip()

def parse_dates(s):
    """Parse dates vectorized - handles both ISO (YYYY-MM-DD) and UK (DD/MM/YYYY) formats"""
    # Try ISO format first, then UK format for unparseable dates
    result = pd.to_datetime(s, format='%Y-%m-%d', errors='coerce')
    # For any that failed, try UK format
    mask_nat = result.isna()
    if mask_nat.any():
        result.loc[mask_nat] = pd.to_datetime(s[mask_nat], format='%d/%m/%Y', errors='coerce')
    return result

def main():
    print("=" * 70)
    print("LCT Actuals Processing v2 - Fast Vectorized (England Only)")
    print("DFES Methodology: Apr 2025 - Mar 2026")
    print("=" * 70)

    # Load lookups
    print("\nLoading Geography Lookups...")

    # Use spatial join postcode-LSOA21 lookup (90%+ match rate)
    pc_path = os.path.join(INPUT_LOOKUPS, "postcode_lsoa21_lookup_spatial.csv")
    try:
        pc_lookup = pd.read_csv(pc_path)
        pc_lookup['pcds_std'] = standardize_postcode(pc_lookup['postcode'])
        pc_lookup = pc_lookup[['pcds_std', 'LSOA21CD']].drop_duplicates()
        pc_lookup.set_index('pcds_std', inplace=True)
        print(f"  Postcode-LSOA21 lookup (spatial): {len(pc_lookup)} rows (England only)")
    except FileNotFoundError:
        print(f"  ERROR: Spatial lookup not found. Run build_postcode_lsoa_lookup.py first")
        exit(1)
    except Exception as e:
        print(f"  ERROR loading spatial lookup: {e}")
        exit(1)

    # Note: No longer need LSOA11->LSOA21 bridge since spatial join gives us LSOA21CD directly
    bridge = None
    print(f"  LSOA11->LSOA21 bridge: skipped (using spatial LSOA21CD directly)")

    # Load LSOA to DNO lookup (also has LAD22CD and MSOA data)
    dno_path = os.path.join(INPUT_LOOKUPS, "LSOA to DNO.csv")
    try:
        dno_lookup = pd.read_csv(dno_path, encoding='utf-8-sig')
        dno_lookup = dno_lookup[['LSOA21CD', 'LAD22CD', 'LAD22NM', 'MSOA21CD', 'MSOA21NM', 'Majority Licence area']].copy()
        dno_lookup.columns = ['LSOA21CD', 'LAD22CD', 'LAD22NM', 'MSOA21CD', 'MSOA21NM', 'DNO']
        dno_lookup.set_index('LSOA21CD', inplace=True)
        print(f"  LSOA21->DNO lookup: {len(dno_lookup)} rows (with LAD22CD, MSOA, DNO)")
    except Exception as e:
        print(f"  Warning: Could not load LSOA to DNO lookup: {e}")
        dno_lookup = None

    # Process MCS files
    print("\n--- MCS Processing (Apr 2025 - Mar 2026) ---")
    mcs_dir = os.path.join(LCT_DIR, "MCS")
    csv_files = sorted(glob.glob(os.path.join(mcs_dir, "*.csv")))
    print(f"Processing {len(csv_files)} MCS files...")

    all_records = []
    total_input = 0
    total_kept = 0

    for i, filepath in enumerate(csv_files, 1):
        filename = os.path.basename(filepath)
        try:
            df = pd.read_csv(filepath)
            total_input += len(df)

            # Parse dates
            df['period'] = parse_dates(df['Commissioning Date']).dt.to_period('M').astype(str)

            # Filter date range
            mask_date = (df['period'] >= '2025-04') & (df['period'] <= '2026-03')
            df = df[mask_date]

            if len(df) == 0:
                print(f"  [{i:2d}] {filename}: 0 records in date range")
                continue

            # Filter technology - heat pump types only
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
                print(f"  [{i:2d}] {filename}: 0 heat pump records")
                continue

            # Standardize postcode
            df['postcode_std'] = standardize_postcode(df['Postcode'])

            # Join postcode lookup (gets LSOA21CD directly from spatial join)
            df = df.join(pc_lookup, on='postcode_std', how='left')
            df = df[df['LSOA21CD'].notna()]

            if len(df) == 0:
                print(f"  [{i:2d}] {filename}: 0 records with valid postcode/LSOA21")
                continue

            # Join DNO lookup (gets LAD22CD, MSOA, DNO, etc.)
            if dno_lookup is not None:
                df = df.join(dno_lookup, on='LSOA21CD', how='left')
            else:
                df['LAD22CD'] = None
                df['LAD22NM'] = None
                df['MSOA21CD'] = None
                df['MSOA21NM'] = None
                df['DNO'] = None

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

            all_records.append(df[['period', 'LSOA21CD', 'LAD22CD', 'MSOA21CD', 'MSOA21NM', 'DNO', 'capacity_kw']])
            total_kept += len(df)
            print(f"  [{i:2d}] {filename}: {len(df)} heat pumps (England)")

        except Exception as e:
            print(f"  [{i:2d}] {filename}: ERROR - {str(e)[:50]}")

    print(f"\nMCS Summary:")
    print(f"  Input records: {total_input}")
    print(f"  Output records (England, valid LSOA21): {total_kept}")
    print(f"  TOTAL HEAT PUMPS: {total_kept}")

    # Aggregate
    if all_records:
        df_all = pd.concat(all_records, ignore_index=True)

        # Detailed LSOA-level aggregation
        agg_lsoa = df_all.groupby(['period', 'LSOA21CD', 'LAD22CD', 'MSOA21CD', 'MSOA21NM', 'DNO']).agg(
            install_count=('capacity_kw', 'count'),
            total_kw=('capacity_kw', 'sum'),
        ).reset_index()

        # DNO-level summary
        agg_dno = df_all.groupby(['period', 'DNO']).agg(
            install_count=('capacity_kw', 'count'),
            total_kw=('capacity_kw', 'sum'),
        ).reset_index().sort_values(['period', 'DNO'])

        os.makedirs(OUTPUT_PROCESSED, exist_ok=True)

        out_path_lsoa = os.path.join(OUTPUT_PROCESSED, "lsoa_lct_actuals_mcs_v2_england.csv")
        agg_lsoa.to_csv(out_path_lsoa, index=False)

        out_path_dno = os.path.join(OUTPUT_PROCESSED, "dno_lct_actuals_mcs_v2_england.csv")
        agg_dno.to_csv(out_path_dno, index=False)

        print(f"\nDetailed output (LSOA level): {out_path_lsoa}")
        print(f"  Rows: {len(agg_lsoa)}")
        print(f"\nDNO summary output: {out_path_dno}")
        print(f"  Rows: {len(agg_dno)}")

        print(f"\nSummary by DNO:")
        for dno in sorted(agg_dno['DNO'].unique()):
            dno_data = agg_dno[agg_dno['DNO'] == dno]
            total_installs = int(dno_data['install_count'].sum())
            total_kw = dno_data['total_kw'].sum()
            print(f"  {str(dno):4s}: {total_installs:6d} installs, {total_kw:10,.0f} kW ({total_kw/1000:6.1f} MW)")

        print(f"\nOverall Summary:")
        print(f"  Date range: {agg_lsoa['period'].min()} to {agg_lsoa['period'].max()}")
        print(f"  Total installs: {int(agg_lsoa['install_count'].sum())}")
        print(f"  Total capacity: {agg_lsoa['total_kw'].sum():.0f} kW ({agg_lsoa['total_kw'].sum()/1000:.1f} MW)")
    else:
        print("\nNo records found!")

    print("\n" + "=" * 70)
    print("Done.")

if __name__ == "__main__":
    main()
