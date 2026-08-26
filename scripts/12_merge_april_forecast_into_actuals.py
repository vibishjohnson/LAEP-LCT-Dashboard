#!/usr/bin/env python3
"""
Merge April 2025 forecast data into actuals as fiscal year opening baseline
For EVs, Heat Pumps, and Solar PV: replace April actuals with forecast values
"""

import os
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "project", "output_processed")

print("=" * 70)
print("Merge April 2025 Forecast into Actuals (Fiscal Year Opening)")
print("=" * 70)

# ============================================================================
# EV: Replace April actuals with forecast
# ============================================================================
print("\n--- EV (LSOA Level) ---")
ev_lsoa = pd.read_csv(os.path.join(OUTPUT_DIR, "ev_actuals_lsoa.csv"), low_memory=False)
ev_forecast = pd.read_csv(os.path.join(OUTPUT_DIR, "dfes_ev_forecast_reduced_demand.csv"))

# Get April 2025 forecast
april_forecast = ev_forecast[ev_forecast['period'] == '2025-04'].copy()
april_forecast = april_forecast[['LSOA21CD', 'DNO', 'vehicle_type', 'forecast_value']].copy()
april_forecast.columns = ['LSOA21CD', 'DNO', 'EV_Type', 'forecast_value']

# Remove April 2025 actuals
ev_lsoa = ev_lsoa[ev_lsoa['period'] != '2025-04'].copy()

# Add April with forecast values
april_with_forecast = april_forecast.copy()
april_with_forecast['period'] = '2025-04'
april_with_forecast['tech_type'] = 'EV'
april_with_forecast['install_count'] = april_with_forecast['forecast_value'].astype(int)
april_with_forecast['total_kw'] = 0.0
april_with_forecast = april_with_forecast[['period', 'tech_type', 'LSOA21CD', 'DNO', 'EV_Type', 'install_count', 'total_kw']]

ev_lsoa_updated = pd.concat([ev_lsoa, april_with_forecast], ignore_index=True)
ev_lsoa_updated = ev_lsoa_updated.sort_values(['period', 'LSOA21CD', 'EV_Type'])
ev_lsoa_updated.to_csv(os.path.join(OUTPUT_DIR, "ev_actuals_lsoa.csv"), index=False)
print(f"EV Actuals updated: {len(ev_lsoa_updated)} records")
print(f"  April 2025 now uses forecast baseline")

# ============================================================================
# EV DNO Level
# ============================================================================
print("\n--- EV (DNO Level) ---")
ev_dno = pd.read_csv(os.path.join(OUTPUT_DIR, "ev_actuals_dno.csv"), low_memory=False)

april_forecast_dno = ev_forecast[ev_forecast['period'] == '2025-04'].copy()
april_forecast_dno = april_forecast_dno.groupby(['DNO', 'vehicle_type'])['forecast_value'].sum().reset_index()
april_forecast_dno.columns = ['DNO', 'EV_Type', 'forecast_value']

ev_dno = ev_dno[ev_dno['period'] != '2025-04'].copy()
april_dno = april_forecast_dno.copy()
april_dno['period'] = '2025-04'
april_dno['tech_type'] = 'EV'
april_dno['install_count'] = april_dno['forecast_value'].astype(int)
april_dno['total_kw'] = 0.0
april_dno = april_dno[['period', 'tech_type', 'DNO', 'EV_Type', 'install_count', 'total_kw']]

ev_dno_updated = pd.concat([ev_dno, april_dno], ignore_index=True)
ev_dno_updated = ev_dno_updated.sort_values(['period', 'DNO', 'EV_Type'])
ev_dno_updated.to_csv(os.path.join(OUTPUT_DIR, "ev_actuals_dno.csv"), index=False)
print(f"EV Actuals updated: {len(ev_dno_updated)} records")

# ============================================================================
# Heat Pumps: Replace April actuals with forecast (LSOA level)
# ============================================================================
print("\n--- Heat Pumps (LSOA Level) ---")
lct_lsoa = pd.read_csv(os.path.join(OUTPUT_DIR, "dashboard_data_lsoa.csv"), low_memory=False)
hp_forecast = pd.read_csv(os.path.join(OUTPUT_DIR, "dfes_heat_pump_forecast_holistic_transition.csv"))

april_hp_forecast = hp_forecast[hp_forecast['period'] == '2025-04'].copy()
april_hp_forecast = april_hp_forecast[['LSOA21CD', 'DNO', 'forecast_value']].copy()

# Remove April actuals for Heat Pumps
lct_lsoa = lct_lsoa[~((lct_lsoa['period'] == '2025-04') & (lct_lsoa['tech_type'] == 'Heat Pump'))].copy()

# Add forecast values for April Heat Pumps
april_hp_with_forecast = april_hp_forecast.copy()
april_hp_with_forecast['period'] = '2025-04'
april_hp_with_forecast['tech_type'] = 'Heat Pump'
april_hp_with_forecast['install_count'] = april_hp_with_forecast['forecast_value'].astype(int)
april_hp_with_forecast['total_kw'] = 0.0
april_hp_with_forecast = april_hp_with_forecast[['period', 'tech_type', 'LSOA21CD', 'DNO', 'install_count', 'total_kw']]

lct_lsoa_updated = pd.concat([lct_lsoa, april_hp_with_forecast], ignore_index=True)

# ============================================================================
# Solar PV: Replace April actuals with forecast (capacity, not count)
# ============================================================================
print("\n--- Solar PV (LSOA Level - Capacity) ---")
pv_forecast = pd.read_csv(os.path.join(OUTPUT_DIR, "dfes_solar_pv_forecast_high.csv"))

april_pv_forecast = pv_forecast[pv_forecast['period'] == '2025-04'].copy()
april_pv_forecast = april_pv_forecast[['LSOA21CD', 'DNO', 'forecast_value']].copy()

# Remove April actuals for Solar PV
lct_lsoa_updated = lct_lsoa_updated[~((lct_lsoa_updated['period'] == '2025-04') & (lct_lsoa_updated['tech_type'] == 'Solar PV'))].copy()

# Add forecast values for April Solar PV (capacity-based)
april_pv_with_forecast = april_pv_forecast.copy()
april_pv_with_forecast['period'] = '2025-04'
april_pv_with_forecast['tech_type'] = 'Solar PV'
april_pv_with_forecast['install_count'] = 0
april_pv_with_forecast = april_pv_with_forecast.rename(columns={'forecast_value': 'total_kw'})
april_pv_with_forecast = april_pv_with_forecast[['period', 'tech_type', 'LSOA21CD', 'DNO', 'install_count', 'total_kw']]

# Final combine and save
lct_lsoa_updated = pd.concat([lct_lsoa_updated, april_pv_with_forecast], ignore_index=True)
lct_lsoa_updated = lct_lsoa_updated.sort_values(['period', 'LSOA21CD', 'tech_type'])

lct_lsoa_updated.to_csv(os.path.join(OUTPUT_DIR, "dashboard_data_lsoa.csv"), index=False)
print(f"Actuals updated: {len(lct_lsoa_updated)} records")
print(f"  April 2025 Heat Pumps now use forecast baseline")
print(f"  April 2025 Solar PV now use forecast baseline (capacity)")

# ============================================================================
# DNO Level: Heat Pump + Solar PV
# ============================================================================
print("\n--- DNO Level ---")
lct_dno = pd.read_csv(os.path.join(OUTPUT_DIR, "dashboard_data_dno.csv"), low_memory=False)

april_hp_dno = hp_forecast[hp_forecast['period'] == '2025-04'].copy()
april_hp_dno = april_hp_dno.groupby('DNO')['forecast_value'].sum().reset_index()
april_hp_dno.columns = ['DNO', 'forecast_value']

april_pv_dno = pv_forecast[pv_forecast['period'] == '2025-04'].copy()
april_pv_dno = april_pv_dno.groupby('DNO')['forecast_value'].sum().reset_index()
april_pv_dno.columns = ['DNO', 'total_kw']

# Remove April Heat Pump and Solar PV actuals
lct_dno = lct_dno[~((lct_dno['period'] == '2025-04') & (lct_dno['tech_type'].isin(['Heat Pump', 'Solar PV'])))].copy()

# Add April Heat Pump forecast
april_hp_dno_rows = april_hp_dno.copy()
april_hp_dno_rows['period'] = '2025-04'
april_hp_dno_rows['tech_type'] = 'Heat Pump'
april_hp_dno_rows['install_count'] = april_hp_dno_rows['forecast_value'].astype(int)
april_hp_dno_rows['total_kw'] = 0.0
april_hp_dno_rows = april_hp_dno_rows[['period', 'tech_type', 'DNO', 'install_count', 'total_kw']]

# Add April Solar PV forecast
april_pv_dno_rows = april_pv_dno.copy()
april_pv_dno_rows['period'] = '2025-04'
april_pv_dno_rows['tech_type'] = 'Solar PV'
april_pv_dno_rows['install_count'] = 0
april_pv_dno_rows = april_pv_dno_rows[['period', 'tech_type', 'DNO', 'install_count', 'total_kw']]

lct_dno_updated = pd.concat([lct_dno, april_hp_dno_rows, april_pv_dno_rows], ignore_index=True)
lct_dno_updated = lct_dno_updated.sort_values(['period', 'DNO', 'tech_type'])

lct_dno_updated.to_csv(os.path.join(OUTPUT_DIR, "dashboard_data_dno.csv"), index=False)
print(f"DNO Actuals updated: {len(lct_dno_updated)} records")

print("\n" + "=" * 70)
print("Done. April 2025 now uses forecast as opening baseline.")
print("=" * 70)
