#!/usr/bin/env python3
"""
DFES Solar PV (Rooftop) Forecast Processing
Installed capacity (kW), interpolated from 2024 to 2025
"""

import os
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DFES_DIR = os.path.join(PROJECT_ROOT, "dfes")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "project", "output_processed")

SCENARIO_WORLD = "Holistic Transition"

print("=" * 70)
print("DFES Solar PV Forecast Processing")
print("=" * 70)

pv_file = os.path.join(DFES_DIR, "DFES 2026", "UKPN-rooftop-PV-scenarios-LSOA-w-LAEP UPDATED.xlsx")

print(f"\nLoading DFES data: {pv_file}")

# Read PV data - skip cover sheets
xls_pv = pd.ExcelFile(pv_file)
sheet_pv = [s for s in xls_pv.sheet_names if s.lower() not in ['cover', 'sheet1', 'qc']][0]
df_pv = pd.read_excel(pv_file, sheet_name=sheet_pv)
df_pv = df_pv[df_pv['Scenario World'] == SCENARIO_WORLD].copy()
print(f"  Total records: {len(df_pv)} (from sheet: {sheet_pv})")

# Extract columns: LSOA21CD, Scenario, 2024, 2025
# Note: PV is likely in MW or kW, check the Parameter/Unit column
df_pv = df_pv[['LSOA21CD', 'Scenario', 'Parameter', 'Unit', 2024, 2025]].copy()

# Convert to numeric
df_pv[2024] = pd.to_numeric(df_pv[2024], errors='coerce').fillna(0)
df_pv[2025] = pd.to_numeric(df_pv[2025], errors='coerce').fillna(0)

print(f"  PV records: {len(df_pv)}")
print(f"  Unique Parameters: {df_pv['Parameter'].unique()[:5] if 'Parameter' in df_pv.columns else 'N/A'}")
print(f"  Unique Units: {df_pv['Unit'].unique() if 'Unit' in df_pv.columns else 'N/A'}")

# Convert to kW if needed (assuming MWp or similar)
# If Unit contains "MW", multiply by 1000
if df_pv['Unit'].str.contains('MW', case=False, na=False).any():
    df_pv[2024] = df_pv[2024] * 1000
    df_pv[2025] = df_pv[2025] * 1000
    print("\n  Converting MW -> kW")

print(f"\nPV capacity 31 Mar 2025 (start): {df_pv[2024].sum():,.0f} kW")
print(f"PV capacity 31 Mar 2026 (end):   {df_pv[2025].sum():,.0f} kW")

# Linear interpolation between 2024 and 2025 for Apr 2025 - Mar 2026
monthly_records = []
months = ['2025-04', '2025-05', '2025-06', '2025-07', '2025-08', '2025-09',
          '2025-10', '2025-11', '2025-12', '2026-01', '2026-02', '2026-03']

for _, row in df_pv.iterrows():
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
            'forecast_kw': interpolated
        })

df_monthly = pd.DataFrame(monthly_records)

print(f"\nMonthly interpolated records: {len(df_monthly)}")

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
df_monthly['tech_type'] = 'Solar PV'
df_monthly['forecast_count'] = 0  # No install count for capacity-based forecast
df_monthly = df_monthly.rename(columns={'forecast_kw': 'forecast_value'})

os.makedirs(OUTPUT_DIR, exist_ok=True)

# Save by scenario
for scenario in sorted(df_monthly['Scenario'].unique()):
    scenario_data = df_monthly[df_monthly['Scenario'] == scenario].copy()
    scenario_data = scenario_data[['period', 'tech_type', 'LSOA21CD', 'DNO', 'forecast_value']].copy()
    scenario_data = scenario_data.sort_values(['period', 'LSOA21CD'])

    scenario_clean = scenario.replace(' ', '_').lower()
    out_path = os.path.join(OUTPUT_DIR, f"dfes_solar_pv_forecast_{scenario_clean}.csv")
    scenario_data.to_csv(out_path, index=False)

    total = scenario_data['forecast_value'].sum()
    print(f"\nDFES Solar PV Forecast ({scenario}): {out_path}")
    print(f"  Total capacity (Apr 2025 - Mar 2026): {total:,.0f} kW")

# Combined
df_combined = df_monthly[['period', 'tech_type', 'LSOA21CD', 'DNO', 'forecast_value', 'Scenario']].copy()
df_combined = df_combined.sort_values(['Scenario', 'period', 'LSOA21CD'])
out_path_combined = os.path.join(OUTPUT_DIR, "dfes_solar_pv_forecast_all.csv")
df_combined.to_csv(out_path_combined, index=False)
print(f"\nDFES Solar PV Forecast (All scenarios): {out_path_combined}")

print("\n" + "=" * 70)
print("Done.")
