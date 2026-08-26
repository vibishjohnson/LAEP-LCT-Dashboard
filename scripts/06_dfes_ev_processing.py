#!/usr/bin/env python3
"""
DFES EV Forecast Processing
Extracts DFES EV forecasts for fiscal year 2025/26 (2025 column), distributes to quarters
"""

import os
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DFES_DIR = os.path.join(PROJECT_ROOT, "dfes")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "project", "output_processed")

# Configuration
SCENARIO_WORLD = "Holistic Transition"

print("=" * 70)
print("DFES EV Forecast Processing")
print("=" * 70)

excel_file = os.path.join(DFES_DIR, "DFES 2026", "UKPN-electric-car-van-scenarios-LSOA UPDATED.xlsx")

if not os.path.exists(excel_file):
    raise FileNotFoundError(f"DFES file not found: {excel_file}")

print(f"\nLoading DFES data: {excel_file}")
print(f"Scenario World: {SCENARIO_WORLD}")
print(f"Period: Apr 2025 - Mar 2026 (interpolating 2024 -> 2025)")

# Read cars and vans sheets
df_cars = pd.read_excel(excel_file, sheet_name='cars')
df_vans = pd.read_excel(excel_file, sheet_name='vans')

print(f"  Cars shape: {df_cars.shape}")
print(f"  Vans shape: {df_vans.shape}")

# Filter to scenario
df_cars = df_cars[df_cars['Scenario World'] == SCENARIO_WORLD].copy()
df_vans = df_vans[df_vans['Scenario World'] == SCENARIO_WORLD].copy()

print(f"  After scenario filter: Cars {df_cars.shape[0]}, Vans {df_vans.shape[0]}")

# Extract BEV + PHEV for both years (2024 = start of FY, 2025 = end of FY)
bev_cars = df_cars[df_cars['Parameter'] == 'BEV cars'][['LSOA21CD', 'Scenario', 2024, 2025]].copy()
phev_cars = df_cars[df_cars['Parameter'] == 'PHEV cars'][['LSOA21CD', 'Scenario', 2024, 2025]].copy()
bev_vans = df_vans[df_vans['Parameter'] == 'BEV Vans'][['LSOA21CD', 'Scenario', 2024, 2025]].copy()
phev_vans = df_vans[df_vans['Parameter'] == 'PHEV Vans'][['LSOA21CD', 'Scenario', 2024, 2025]].copy()

# Add labels
bev_cars['vehicle_type'] = 'Cars (BEV)'
phev_cars['vehicle_type'] = 'Cars (PHEV)'
bev_vans['vehicle_type'] = 'Vans (BEV)'
phev_vans['vehicle_type'] = 'Vans (PHEV)'

# Combine all
df_all = pd.concat([bev_cars, phev_cars, bev_vans, phev_vans], ignore_index=True)
df_all.columns = ['LSOA21CD', 'Scenario', 'value_2024', 'value_2025', 'vehicle_type']
df_all['value_2024'] = pd.to_numeric(df_all['value_2024'], errors='coerce').fillna(0).astype(int)
df_all['value_2025'] = pd.to_numeric(df_all['value_2025'], errors='coerce').fillna(0).astype(int)

print(f"\nTotal records: {len(df_all)}")
print(f"EV stock 31 Mar 2025 (start): {df_all['value_2024'].sum():,}")
print(f"EV stock 31 Mar 2026 (end):   {df_all['value_2025'].sum():,}")

# Linear interpolation between 2024 (31 Mar 2025) and 2025 (31 Mar 2026)
# Create monthly values for Apr 2025 - Mar 2026 (12 months)
monthly_records = []
months = ['2025-04', '2025-05', '2025-06', '2025-07', '2025-08', '2025-09',
          '2025-10', '2025-11', '2025-12', '2026-01', '2026-02', '2026-03']

for _, row in df_all.iterrows():
    val_start = row['value_2024']
    val_end = row['value_2025']
    total_change = val_end - val_start

    for month_idx, month in enumerate(months):
        # Linear interpolation: 0 at month 0 (Apr), 1 at month 11 (Mar)
        progress = month_idx / 11
        interpolated = val_start + (total_change * progress)

        monthly_records.append({
            'period': month,
            'LSOA21CD': row['LSOA21CD'],
            'Scenario': row['Scenario'],
            'vehicle_type': row['vehicle_type'],
            'forecast_count': int(round(interpolated))
        })

df_monthly = pd.DataFrame(monthly_records)

print(f"Monthly interpolated records: {len(df_monthly)}")

# Add DNO from LSOA mapping
dno_lookup = pd.read_csv(
    os.path.join(PROJECT_ROOT, "lookups", "LSOA to DNO.csv"),
    encoding='utf-8-sig',
    usecols=['LSOA21CD', 'Majority Licence area']
)
dno_lookup.columns = ['LSOA21CD', 'DNO']
dno_lookup = dno_lookup.drop_duplicates()

df_monthly = df_monthly.merge(dno_lookup, on='LSOA21CD', how='left')

# Format for dashboard
df_monthly['tech_type'] = 'EV'
df_monthly['total_kw'] = 0.0  # No capacity data for forecasts
df_monthly = df_monthly.rename(columns={'forecast_count': 'forecast_value'})

os.makedirs(OUTPUT_DIR, exist_ok=True)

# Save outputs by scenario
for scenario in sorted(df_monthly['Scenario'].unique()):
    scenario_data = df_monthly[df_monthly['Scenario'] == scenario].copy()
    scenario_data = scenario_data[['period', 'tech_type', 'LSOA21CD', 'DNO', 'forecast_value', 'vehicle_type']].copy()
    scenario_data = scenario_data.sort_values(['period', 'LSOA21CD'])

    # Clean scenario name for filename
    scenario_clean = scenario.replace(' ', '_').lower()
    out_path = os.path.join(OUTPUT_DIR, f"dfes_ev_forecast_{scenario_clean}.csv")
    scenario_data.to_csv(out_path, index=False)

    total_forecast = scenario_data['forecast_value'].sum()
    print(f"\nDFES EV Forecast ({scenario}): {out_path}")
    print(f"  Total (Apr 2025 - Mar 2026): {total_forecast:,}")

# Also save combined for dashboard
df_combined = df_monthly[['period', 'tech_type', 'LSOA21CD', 'DNO', 'forecast_value', 'Scenario']].copy()
df_combined = df_combined.sort_values(['Scenario', 'period', 'LSOA21CD'])
out_path_combined = os.path.join(OUTPUT_DIR, "dfes_ev_forecast_all.csv")
df_combined.to_csv(out_path_combined, index=False)
print(f"\nDFES EV Forecast (All scenarios combined): {out_path_combined}")

print("\n" + "=" * 70)
print("Done. Scenarios available:")
for scenario in sorted(df_monthly['Scenario'].unique()):
    print(f"  - {scenario}")
