#!/usr/bin/env python3
"""
Merge March 2025 forecast data into actuals as baseline
For EVs, Heat Pumps, and Solar PV: replace March actuals with forecast values
For Solar PV: use capacity instead of counts
"""

import os
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "project", "output_processed")

print("=" * 70)
print("Merge March 2025 Forecast into Actuals Baseline")
print("=" * 70)

# ============================================================================
# EV: Replace March actuals with forecast
# ============================================================================
print("\n--- EV (LSOA Level) ---")
ev_actuals = pd.read_csv(os.path.join(OUTPUT_DIR, "ev_actuals_lsoa.csv"))
ev_forecast = pd.read_csv(os.path.join(OUTPUT_DIR, "dfes_ev_forecast_reduced_demand.csv"))

print(f"EV Actuals before: {len(ev_actuals)} records")

# Get March 2026 forecast (fiscal year end)
march_forecast = ev_forecast[ev_forecast['period'] == '2026-03'].copy()
march_forecast = march_forecast[['LSOA21CD', 'DNO', 'vehicle_type', 'forecast_value']].copy()
march_forecast.columns = ['LSOA21CD', 'DNO', 'EV_Type', 'march_forecast']

print(f"March 2026 forecast records: {len(march_forecast)}")

# Remove March 2026 actuals for EV and replace with forecast
ev_actuals = ev_actuals[ev_actuals['period'] != '2026-03'].copy()

# Add forecast values for March
march_with_forecast = march_forecast.copy()
march_with_forecast['period'] = '2026-03'
march_with_forecast['tech_type'] = 'EV'
march_with_forecast['install_count'] = march_with_forecast['march_forecast'].astype(int)
march_with_forecast['total_kw'] = 0.0
march_with_forecast = march_with_forecast[['period', 'tech_type', 'LSOA21CD', 'DNO', 'EV_Type', 'install_count', 'total_kw']]

# Combine and save
ev_actuals_updated = pd.concat([ev_actuals, march_with_forecast], ignore_index=True)
ev_actuals_updated = ev_actuals_updated.sort_values(['period', 'LSOA21CD', 'DNO', 'EV_Type'])

out_path = os.path.join(OUTPUT_DIR, "ev_actuals_lsoa.csv")
ev_actuals_updated.to_csv(out_path, index=False)
print(f"EV Actuals updated: {len(ev_actuals_updated)} records")
print(f"  March 2026 now uses forecast baseline")

# ============================================================================
# Heat Pumps: Replace March actuals with forecast (LSOA level)
# ============================================================================
print("\n--- Heat Pumps (LSOA Level) ---")
actuals = pd.read_csv(os.path.join(OUTPUT_DIR, "dashboard_data_lsoa.csv"))
hp_forecast = pd.read_csv(os.path.join(OUTPUT_DIR, "dfes_heat_pump_forecast_holistic_transition.csv"))

print(f"Total Actuals before: {len(actuals)} records")

# Get March 2026 forecast for Heat Pumps
march_hp_forecast = hp_forecast[hp_forecast['period'] == '2026-03'].copy()
march_hp_forecast = march_hp_forecast[['LSOA21CD', 'DNO', 'forecast_value']].copy()
march_hp_forecast.columns = ['LSOA21CD', 'DNO', 'march_forecast_hp']

print(f"March 2026 HP forecast records: {len(march_hp_forecast)}")

# Remove March actuals for Heat Pumps
actuals = actuals[~((actuals['period'] == '2026-03') & (actuals['tech_type'] == 'Heat Pump'))].copy()

# Add forecast values for March Heat Pumps
march_hp_with_forecast = march_hp_forecast.copy()
march_hp_with_forecast['period'] = '2026-03'
march_hp_with_forecast['tech_type'] = 'Heat Pump'
march_hp_with_forecast['install_count'] = march_hp_with_forecast['march_forecast_hp'].astype(int)
march_hp_with_forecast['total_kw'] = 0.0
march_hp_with_forecast = march_hp_with_forecast[['period', 'tech_type', 'LSOA21CD', 'DNO', 'install_count', 'total_kw']]

# Combine and save
actuals_updated = pd.concat([actuals, march_hp_with_forecast], ignore_index=True)

# ============================================================================
# Solar PV: Replace March actuals with forecast (capacity, not count)
# ============================================================================
print("\n--- Solar PV (LSOA Level - Capacity) ---")
pv_forecast = pd.read_csv(os.path.join(OUTPUT_DIR, "dfes_solar_pv_forecast_high.csv"))

print(f"Total Actuals before: {len(actuals_updated)} records")

# Get March 2026 forecast for Solar PV (use capacity/forecast_value)
march_pv_forecast = pv_forecast[pv_forecast['period'] == '2026-03'].copy()
march_pv_forecast = march_pv_forecast[['LSOA21CD', 'DNO', 'forecast_value']].copy()
march_pv_forecast.columns = ['LSOA21CD', 'DNO', 'total_kw']

print(f"March 2026 PV forecast records: {len(march_pv_forecast)}")

# Remove March actuals for Solar PV
actuals_updated = actuals_updated[~((actuals_updated['period'] == '2026-03') & (actuals_updated['tech_type'] == 'Solar PV'))].copy()

# Add forecast values for March Solar PV (capacity-based)
march_pv_with_forecast = march_pv_forecast.copy()
march_pv_with_forecast['period'] = '2026-03'
march_pv_with_forecast['tech_type'] = 'Solar PV'
march_pv_with_forecast['install_count'] = 0  # No install count for capacity-based
march_pv_with_forecast = march_pv_with_forecast[['period', 'tech_type', 'LSOA21CD', 'DNO', 'install_count', 'total_kw']]

# Final combine and save
actuals_updated = pd.concat([actuals_updated, march_pv_with_forecast], ignore_index=True)
actuals_updated = actuals_updated.sort_values(['period', 'LSOA21CD', 'tech_type'])

out_path = os.path.join(OUTPUT_DIR, "dashboard_data_lsoa.csv")
actuals_updated.to_csv(out_path, index=False)
print(f"Actuals updated: {len(actuals_updated)} records")
print(f"  March 2026 Heat Pumps now use forecast baseline")
print(f"  March 2026 Solar PV now use forecast baseline (capacity)")

print("\n" + "=" * 70)
print("Done. March 2025 now uses forecast as baseline for all tech types.")
print("=" * 70)
