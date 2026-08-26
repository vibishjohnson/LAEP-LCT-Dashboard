#!/usr/bin/env python3
"""
DFES Heat Pump Forecast Processing
Domestic + I&C heating, interpolated from 2024 to 2025
"""

import os
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DFES_DIR = os.path.join(PROJECT_ROOT, "dfes")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "project", "output_processed")

SCENARIO_WORLD = "HolisticTransition"

print("=" * 70)
print("DFES Heat Pump Forecast Processing")
print("=" * 70)

# Read domestic and I&C heating files from DFES 2026
domestic_file = os.path.join(DFES_DIR, "DFES 2026", "UKPN-domestic-heating-scenarios-LSOA-w-LAEP.xlsx")
ic_file = os.path.join(DFES_DIR, "DFES 2026", "UKPN-IC-district-heating-scenarios-LSOA-w-LAEP.xlsx")

print(f"\nLoading DFES data:")
print(f"  Domestic: {domestic_file}")
print(f"  I&C: {ic_file}")

# Read domestic - skip cover sheets
xls_dom = pd.ExcelFile(domestic_file)
sheet_dom = [s for s in xls_dom.sheet_names if s.lower() not in ['cover', 'sheet1', 'qc']][0]
df_domestic = pd.read_excel(domestic_file, sheet_name=sheet_dom)
df_domestic = df_domestic[df_domestic['Scenario World'] == SCENARIO_WORLD].copy()
print(f"  Domestic records: {len(df_domestic)} (from sheet: {sheet_dom})")

# Read I&C
xls_ic = pd.ExcelFile(ic_file)
sheet_ic = [s for s in xls_ic.sheet_names if s.lower() not in ['cover', 'sheet1', 'qc']][0]
df_ic = pd.read_excel(ic_file, sheet_name=sheet_ic)
df_ic = df_ic[df_ic['Scenario World'] == SCENARIO_WORLD].copy()
print(f"  I&C records: {len(df_ic)} (from sheet: {sheet_ic})")

# Filter to heat pump data (look for "heat pump" in Parameter)
def filter_heat_pumps(df):
    """Filter to heat pump records (domestic + hybrid)"""
    mask = df['Parameter'].str.contains('heat pump', case=False, na=False)
    return df[mask].copy()

df_hp_domestic = filter_heat_pumps(df_domestic)
df_hp_ic = filter_heat_pumps(df_ic)

print(f"\nHeat Pump records:")
print(f"  Domestic: {len(df_hp_domestic)}")
print(f"  I&C: {len(df_hp_ic)}")

# Extract columns: LSOA21CD, Scenario, 2024, 2025
df_hp_domestic = df_hp_domestic[['LSOA21CD', 'Scenario World', 2024, 2025]].copy()
df_hp_domestic['sector'] = 'Domestic'
df_hp_domestic = df_hp_domestic.rename(columns={'Scenario World': 'Scenario'})

df_hp_ic = df_hp_ic[['LSOA21CD', 'Scenario World', 2024, 2025]].copy()
df_hp_ic['sector'] = 'I&C'
df_hp_ic = df_hp_ic.rename(columns={'Scenario World': 'Scenario'})

# Combine domestic and I&C
df_all = pd.concat([df_hp_domestic, df_hp_ic], ignore_index=True)

# Convert to numeric
df_all[2024] = pd.to_numeric(df_all[2024], errors='coerce').fillna(0).astype(int)
df_all[2025] = pd.to_numeric(df_all[2025], errors='coerce').fillna(0).astype(int)

print(f"\nTotal Heat Pump records: {len(df_all)}")
print(f"HP count 31 Mar 2025 (start): {df_all[2024].sum():,}")
print(f"HP count 31 Mar 2026 (end):   {df_all[2025].sum():,}")

# Linear interpolation between 2024 and 2025 for Apr 2025 - Mar 2026
monthly_records = []
months = ['2025-04', '2025-05', '2025-06', '2025-07', '2025-08', '2025-09',
          '2025-10', '2025-11', '2025-12', '2026-01', '2026-02', '2026-03']

for _, row in df_all.iterrows():
    val_start = row[2024]
    val_end = row[2025]
    total_change = val_end - val_start

    for month_idx, month in enumerate(months):
        progress = month_idx / 11
        interpolated = val_start + (total_change * progress)

        monthly_records.append({
            'period': month,
            'LSOA21CD': row['LSOA21CD'],
            'Scenario': row['Scenario'],
            'sector': row['sector'],
            'forecast_count': int(round(interpolated))
        })

df_monthly = pd.DataFrame(monthly_records)

print(f"Monthly interpolated records: {len(df_monthly)}")

# Add DNO
dno_lookup = pd.read_csv(
    os.path.join(PROJECT_ROOT, "lookups", "LSOA to DNO.csv"),
    encoding='utf-8-sig',
    usecols=['LSOA21CD', 'Majority Licence area']
)
dno_lookup.columns = ['LSOA21CD', 'DNO']
dno_lookup = dno_lookup.drop_duplicates()

df_monthly = df_monthly.merge(dno_lookup, on='LSOA21CD', how='left')

# Format for dashboard
df_monthly['tech_type'] = 'Heat Pump'
df_monthly['total_kw'] = 0.0
df_monthly = df_monthly.rename(columns={'forecast_count': 'forecast_value'})

os.makedirs(OUTPUT_DIR, exist_ok=True)

# Save by scenario
for scenario in sorted(df_monthly['Scenario'].unique()):
    scenario_data = df_monthly[df_monthly['Scenario'] == scenario].copy()
    scenario_data = scenario_data[['period', 'tech_type', 'LSOA21CD', 'DNO', 'forecast_value', 'sector']].copy()
    scenario_data = scenario_data.sort_values(['period', 'LSOA21CD'])

    scenario_clean = scenario.replace(' ', '_').lower()
    out_path = os.path.join(OUTPUT_DIR, f"dfes_heat_pump_forecast_{scenario_clean}.csv")
    scenario_data.to_csv(out_path, index=False)

    total = scenario_data['forecast_value'].sum()
    print(f"\nDFES Heat Pump Forecast ({scenario}): {out_path}")
    print(f"  Total (Apr 2025 - Mar 2026): {total:,}")

# Combined
df_combined = df_monthly[['period', 'tech_type', 'LSOA21CD', 'DNO', 'forecast_value', 'Scenario']].copy()
df_combined = df_combined.sort_values(['Scenario', 'period', 'LSOA21CD'])
out_path_combined = os.path.join(OUTPUT_DIR, "dfes_heat_pump_forecast_all.csv")
df_combined.to_csv(out_path_combined, index=False)
print(f"\nDFES Heat Pump Forecast (All scenarios): {out_path_combined}")

print("\n" + "=" * 70)
print("Done.")
