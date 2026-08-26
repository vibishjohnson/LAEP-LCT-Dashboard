#!/usr/bin/env python3
"""
Apply cumulative calculations to heat pump actuals
Ensure EV actuals maintain stock levels (no drops to 0)
Then merge April 2025 forecast baseline
"""

import os
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "project", "output_processed")

print("=" * 70)
print("Apply Cumulative Actuals + April Forecast Baseline")
print("=" * 70)

# ============================================================================
# Heat Pump: Convert to cumulative stock (not monthly installations)
# ============================================================================
print("\n--- Heat Pump: Converting to Cumulative ---")

lct_lsoa = pd.read_csv(os.path.join(OUTPUT_DIR, "dashboard_data_lsoa.csv"), low_memory=False)
lct_dno = pd.read_csv(os.path.join(OUTPUT_DIR, "dashboard_data_dno.csv"), low_memory=False)

# LSOA level
hp_lsoa = lct_lsoa[lct_lsoa['tech_type'] == 'Heat Pump'].copy()
other_lsoa = lct_lsoa[lct_lsoa['tech_type'] != 'Heat Pump'].copy()

periods_ordered = ['2025-03', '2025-04', '2025-05', '2025-06', '2025-07', '2025-08',
                   '2025-09', '2025-10', '2025-11', '2025-12', '2026-01', '2026-02']

hp_lsoa['period'] = pd.Categorical(hp_lsoa['period'], categories=periods_ordered, ordered=True)
hp_lsoa = hp_lsoa.sort_values(['LSOA21CD', 'period']).reset_index(drop=True)

# Cumulative sum per LSOA
hp_lsoa['install_count'] = hp_lsoa.groupby('LSOA21CD')['install_count'].cumsum()

print(f"  LSOA Heat Pump: {len(hp_lsoa):,} records, cumulative by LSOA")
print(f"  Monthly totals: {dict(zip(hp_lsoa.groupby('period')['install_count'].sum().index, hp_lsoa.groupby('period')['install_count'].sum().values))}")

# DNO level
hp_dno = lct_dno[lct_dno['tech_type'] == 'Heat Pump'].copy()
other_dno = lct_dno[lct_dno['tech_type'] != 'Heat Pump'].copy()

hp_dno['period'] = pd.Categorical(hp_dno['period'], categories=periods_ordered, ordered=True)
hp_dno = hp_dno.sort_values(['DNO', 'period']).reset_index(drop=True)

hp_dno['install_count'] = hp_dno.groupby('DNO')['install_count'].cumsum()

print(f"  DNO Heat Pump: {len(hp_dno):,} records, cumulative by DNO")

# ============================================================================
# Merge April 2025 Forecast Baseline for Heat Pump
# ============================================================================
print("\n--- Applying April 2025 Forecast Baseline ---")

hp_forecast = pd.read_csv(os.path.join(OUTPUT_DIR, "dfes_heat_pump_forecast_holistic_transition.csv"))
april_hp_forecast = hp_forecast[hp_forecast['period'] == '2025-04'].copy()

# LSOA level - replace April cumulative with forecast baseline
april_hp_lsoa = april_hp_forecast[['LSOA21CD', 'DNO', 'forecast_value']].copy()
hp_lsoa_no_april = hp_lsoa[hp_lsoa['period'] != '2025-04'].copy()

april_hp_lsoa['period'] = '2025-04'
april_hp_lsoa['tech_type'] = 'Heat Pump'
april_hp_lsoa['install_count'] = april_hp_lsoa['forecast_value'].astype(int)
april_hp_lsoa['total_kw'] = 0.0
april_hp_lsoa = april_hp_lsoa[['period', 'tech_type', 'LSOA21CD', 'DNO', 'install_count', 'total_kw']]

hp_lsoa_updated = pd.concat([hp_lsoa_no_april, april_hp_lsoa], ignore_index=True)
hp_lsoa_updated = hp_lsoa_updated.sort_values(['period', 'LSOA21CD', 'tech_type'])

print(f"  April 2025 HP baseline: {april_hp_lsoa['install_count'].sum():,}")

# DNO level - replace April cumulative with forecast baseline
april_hp_dno = april_hp_forecast.groupby('DNO')['forecast_value'].sum().reset_index()
hp_dno_no_april = hp_dno[hp_dno['period'] != '2025-04'].copy()

april_hp_dno['period'] = '2025-04'
april_hp_dno['tech_type'] = 'Heat Pump'
april_hp_dno['install_count'] = april_hp_dno['forecast_value'].astype(int)
april_hp_dno['total_kw'] = 0.0
april_hp_dno = april_hp_dno[['period', 'tech_type', 'DNO', 'install_count', 'total_kw']]

hp_dno_updated = pd.concat([hp_dno_no_april, april_hp_dno], ignore_index=True)
hp_dno_updated = hp_dno_updated.sort_values(['period', 'DNO', 'tech_type'])

# Recombine with other tech types
lct_lsoa_final = pd.concat([other_lsoa, hp_lsoa_updated], ignore_index=True)
lct_lsoa_final = lct_lsoa_final.sort_values(['period', 'LSOA21CD', 'tech_type'])
lct_lsoa_final.to_csv(os.path.join(OUTPUT_DIR, "dashboard_data_lsoa.csv"), index=False)

lct_dno_final = pd.concat([other_dno, hp_dno_updated], ignore_index=True)
lct_dno_final = lct_dno_final.sort_values(['period', 'DNO', 'tech_type'])
lct_dno_final.to_csv(os.path.join(OUTPUT_DIR, "dashboard_data_dno.csv"), index=False)

print(f"\nLCT LSOA updated: {len(lct_lsoa_final):,} records")
print(f"LCT DNO updated: {len(lct_dno_final):,} records")

# ============================================================================
# EV: Apply April 2025 Forecast Baseline (ensure no drops in stock)
# ============================================================================
print("\n--- EV: Applying April 2025 Forecast Baseline ---")

ev_lsoa = pd.read_csv(os.path.join(OUTPUT_DIR, "ev_actuals_lsoa.csv"), low_memory=False)
ev_dno = pd.read_csv(os.path.join(OUTPUT_DIR, "ev_actuals_dno.csv"), low_memory=False)

ev_forecast = pd.read_csv(os.path.join(OUTPUT_DIR, "dfes_ev_forecast_reduced_demand.csv"))
april_ev_forecast = ev_forecast[ev_forecast['period'] == '2025-04'].copy()

# LSOA level
ev_no_april = ev_lsoa[ev_lsoa['period'] != '2025-04'].copy()
april_ev_lsoa = april_ev_forecast[['LSOA21CD', 'DNO', 'vehicle_type', 'forecast_value']].copy()
april_ev_lsoa.columns = ['LSOA21CD', 'DNO', 'EV_Type', 'forecast_value']

april_ev_lsoa['period'] = '2025-04'
april_ev_lsoa['tech_type'] = 'EV'
april_ev_lsoa['install_count'] = april_ev_lsoa['forecast_value'].astype(int)
april_ev_lsoa['total_kw'] = 0.0
april_ev_lsoa = april_ev_lsoa[['period', 'tech_type', 'LSOA21CD', 'DNO', 'EV_Type', 'install_count', 'total_kw']]

ev_lsoa_updated = pd.concat([ev_no_april, april_ev_lsoa], ignore_index=True)
ev_lsoa_updated = ev_lsoa_updated.sort_values(['period', 'LSOA21CD', 'EV_Type'])
ev_lsoa_updated.to_csv(os.path.join(OUTPUT_DIR, "ev_actuals_lsoa.csv"), index=False)

print(f"  April 2025 EV baseline: {april_ev_lsoa['install_count'].sum():,}")
print(f"EV LSOA updated: {len(ev_lsoa_updated):,} records")

# DNO level
ev_dno_no_april = ev_dno[ev_dno['period'] != '2025-04'].copy()
april_ev_dno = april_ev_forecast.groupby(['DNO', 'vehicle_type'])['forecast_value'].sum().reset_index()
april_ev_dno.columns = ['DNO', 'EV_Type', 'forecast_value']

april_ev_dno['period'] = '2025-04'
april_ev_dno['tech_type'] = 'EV'
april_ev_dno['install_count'] = april_ev_dno['forecast_value'].astype(int)
april_ev_dno['total_kw'] = 0.0
april_ev_dno = april_ev_dno[['period', 'tech_type', 'DNO', 'EV_Type', 'install_count', 'total_kw']]

ev_dno_updated = pd.concat([ev_dno_no_april, april_ev_dno], ignore_index=True)
ev_dno_updated = ev_dno_updated.sort_values(['period', 'DNO', 'EV_Type'])
ev_dno_updated.to_csv(os.path.join(OUTPUT_DIR, "ev_actuals_dno.csv"), index=False)

print(f"EV DNO updated: {len(ev_dno_updated):,} records")

print("\n" + "=" * 70)
print("Done! Heat pumps are cumulative, April 2025 baseline applied.")
print("=" * 70)
