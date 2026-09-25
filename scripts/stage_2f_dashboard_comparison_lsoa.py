#!/usr/bin/env python3
"""
Stage 2F: Dashboard Comparison Data - LSOA Level (Actuals vs DFES Benchmarks)
Extends DNO comparison to LSOA granularity, preserving all 11,023 canonical LSOAs.
"""

import pandas as pd
import numpy as np
import os
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "project", "output_processed")

print("=" * 100)
print("STAGE 2F: DASHBOARD COMPARISON DATA - LSOA LEVEL")
print("=" * 100)
print(f"Timestamp: {datetime.utcnow().isoformat()}Z")

# ============================================================================
# STEP 0: LOAD INPUTS AND CANONICAL GEOGRAPHY
# ============================================================================
print("\n0. LOADING INPUTS AND CANONICAL GEOGRAPHY")
print("-" * 100)

# Load canonical LSOA lookup to restrict to canonical geography
lsoa_lookup = pd.read_csv(os.path.join(PROJECT_ROOT, "lookups", "LSOA to DNO.csv"),
                          encoding='utf-8-sig', low_memory=False)
canonical_lsoas = set(lsoa_lookup['LSOA21CD'].unique())
lsoa_to_dno = dict(zip(lsoa_lookup['LSOA21CD'], lsoa_lookup['Majority Licence area']))
print(f"Canonical UKPN LSOAs: {len(canonical_lsoas):,}")

# Load canonical LSOA actuals (all technologies)
lsoa_actuals = pd.read_csv(os.path.join(OUTPUT_DIR, "dashboard_data_lsoa.csv"), low_memory=False)
print(f"Stage 2E LSOA actuals (raw): {len(lsoa_actuals):,} rows")
print(f"  Technologies: {sorted(lsoa_actuals['tech_type'].unique())}")

# Filter to canonical LSOA geometry
lsoa_actuals = lsoa_actuals[lsoa_actuals['LSOA21CD'].isin(canonical_lsoas)].copy()
print(f"Stage 2E LSOA actuals (canonical only): {len(lsoa_actuals):,} rows")
non_canonical_removed = len(lsoa_lookup) - len(lsoa_actuals)
print(f"Non-canonical rows removed: {non_canonical_removed:,}")

# Load DFES EV forecast (LSOA level with vehicle_type)
ev_forecast = pd.read_csv(os.path.join(OUTPUT_DIR, "dfes_ev_forecast_all.csv"))
ev_forecast = ev_forecast[ev_forecast['LSOA21CD'].isin(canonical_lsoas)].copy()
print(f"DFES EV forecast (canonical only): {len(ev_forecast):,} rows")

# ============================================================================
# STEP 1: NON-EV TECHNOLOGIES (Calculate cumulative counts and add stock fields)
# ============================================================================
print("\n1. PROCESSING NON-EV TECHNOLOGIES")
print("-" * 100)

# Load DFES forecasts (full monthly progression)
hp_forecast = pd.read_csv(os.path.join(OUTPUT_DIR, "dfes_heat_pump_forecast_holistic_transition.csv"))
pv_forecast = pd.read_csv(os.path.join(OUTPUT_DIR, "dfes_solar_pv_forecast_high.csv"))

# Extract DFES opening/closing stock by DNO (from Apr/Mar total)
hp_apr = hp_forecast[hp_forecast['period'] == '2025-04'].groupby('DNO')['forecast_value'].sum().to_dict()
hp_mar = hp_forecast[hp_forecast['period'] == '2026-03'].groupby('DNO')['forecast_value'].sum().to_dict()
pv_apr = pv_forecast[pv_forecast['period'] == '2025-04'].groupby('DNO')['forecast_value'].sum().to_dict()
pv_mar = pv_forecast[pv_forecast['period'] == '2026-03'].groupby('DNO')['forecast_value'].sum().to_dict()

# Build LSOA-level DFES monthly/cumulative lookups
def build_dfes_lsoa_lookup(forecast_df, tech_name):
    """Build dict: (LSOA, period) → (monthly_uptake, cumulative_uptake)"""
    lookup = {}
    months = ['2025-04', '2025-05', '2025-06', '2025-07', '2025-08', '2025-09',
              '2025-10', '2025-11', '2025-12', '2026-01', '2026-02', '2026-03']

    for lsoa in forecast_df['LSOA21CD'].unique():
        lsoa_data = forecast_df[forecast_df['LSOA21CD'] == lsoa].sort_values('period')
        prev_stock = None
        cumulative_uptake = 0

        for month in months:
            month_data = lsoa_data[lsoa_data['period'] == month]
            if len(month_data) == 0:
                continue

            current_stock = month_data['forecast_value'].sum()

            # April: use it as opening, uptake is 0 (or difference from prior Mar)
            if month == '2025-04':
                monthly_uptake = 0  # First month uses opening as starting point
            else:
                monthly_uptake = current_stock - prev_stock if prev_stock is not None else 0

            cumulative_uptake += monthly_uptake

            lookup[(lsoa, month)] = {
                'monthly': monthly_uptake,
                'cumulative': cumulative_uptake,
                'stock': current_stock
            }

            prev_stock = current_stock

    return lookup

hp_dfes_lookup = build_dfes_lsoa_lookup(hp_forecast, 'Heat Pump')
pv_dfes_lookup = build_dfes_lsoa_lookup(pv_forecast, 'Solar PV')

lsoa_rows = []
months = ['2025-04', '2025-05', '2025-06', '2025-07', '2025-08', '2025-09',
          '2025-10', '2025-11', '2025-12', '2026-01', '2026-02', '2026-03']

# Heat Pump - rows for union of actual + DFES LSOAs
hp_lsoa_actuals = lsoa_actuals[lsoa_actuals['tech_type'] == 'Heat Pump'].copy()
hp_lsoa_actuals = hp_lsoa_actuals.sort_values('period')

# Build actual data lookup
hp_actual_lookup = {}
for _, row in hp_lsoa_actuals.iterrows():
    key = (row['LSOA21CD'], row['period'])
    hp_actual_lookup[key] = row

# Union: LSOAs with actual data OR DFES forecast
hp_actual_lsoas = set(hp_lsoa_actuals['LSOA21CD'].unique())
hp_dfes_lsoas = set([lsoa for lsoa, _ in hp_dfes_lookup.keys()])
hp_lsoas_to_process = (hp_actual_lsoas | hp_dfes_lsoas) & canonical_lsoas

hp_count = 0
for lsoa in sorted(hp_lsoas_to_process):
    dno = lsoa_to_dno.get(lsoa, 'Unknown')

    # Get LAEP from actual data if available
    lsoa_actuals_for_laep = hp_lsoa_actuals[hp_lsoa_actuals['LSOA21CD'] == lsoa]
    laep = lsoa_actuals_for_laep.iloc[0]['LAEP'] if len(lsoa_actuals_for_laep) > 0 else None

    # Process all 12 months for this LSOA
    for month in months:
        # Get actual data for this month if it exists
        actual_row = hp_actual_lookup.get((lsoa, month))
        actual_install_count = actual_row['install_count'] if actual_row is not None else None
        actual_total_kw = actual_row['total_kw'] if actual_row is not None else None

        # Get DFES data for this month if it exists
        dfes_info = hp_dfes_lookup.get((lsoa, month), {})
        dfes_monthly = dfes_info.get('monthly')
        dfes_cumulative = dfes_info.get('cumulative')

        # Only create row if ACTUAL data OR DFES data exists (locked data contract)
        if actual_install_count is not None or dfes_monthly is not None or dfes_cumulative is not None:
            lsoa_rows.append({
                'period': month,
                'tech_type': 'Heat Pump',
                'LSOA21CD': lsoa,
                'DNO': dno,
                'LAEP': laep,
                'actual_install_count': actual_install_count,
                'actual_total_kw': actual_total_kw,
                'actual_cumulative_count': None,
                'actual_cumulative_kw': None,
                'dfes_monthly_benchmark': dfes_monthly,
                'dfes_cumulative_benchmark': dfes_cumulative,
                'dfes_scenario': None,
                'comparison_unit': 'Number of installations',
                'EV_Type': None,
                'variance': None,
                'variance_pct': None,
                'comparison_point_type': None,
                'dfes_opening_stock': hp_apr.get(dno, None),
                'dfes_closing_stock': hp_mar.get(dno, None)
            })
            hp_count += 1

print(f"Heat Pump LSOA rows: {hp_count:,}")

# Solar PV - rows for union of actual + DFES LSOAs
pv_lsoa_actuals = lsoa_actuals[lsoa_actuals['tech_type'] == 'Solar PV'].copy()
pv_lsoa_actuals = pv_lsoa_actuals.sort_values('period')

# Build actual data lookup
pv_actual_lookup = {}
for _, row in pv_lsoa_actuals.iterrows():
    key = (row['LSOA21CD'], row['period'])
    pv_actual_lookup[key] = row

# Union: LSOAs with actual data OR DFES forecast
pv_actual_lsoas = set(pv_lsoa_actuals['LSOA21CD'].unique())
pv_dfes_lsoas = set([lsoa for lsoa, _ in pv_dfes_lookup.keys()])
pv_lsoas_to_process = (pv_actual_lsoas | pv_dfes_lsoas) & canonical_lsoas

pv_count = 0
for lsoa in sorted(pv_lsoas_to_process):
    dno = lsoa_to_dno.get(lsoa, 'Unknown')

    # Get LAEP from actual data if available
    lsoa_actuals_for_laep = pv_lsoa_actuals[pv_lsoa_actuals['LSOA21CD'] == lsoa]
    laep = lsoa_actuals_for_laep.iloc[0]['LAEP'] if len(lsoa_actuals_for_laep) > 0 else None

    # Process all 12 months for this LSOA
    for month in months:
        # Get actual data for this month if it exists
        actual_row = pv_actual_lookup.get((lsoa, month))
        actual_install_count = actual_row['install_count'] if actual_row is not None else None
        actual_total_kw = actual_row['total_kw'] if actual_row is not None else None

        # Get DFES data for this month if it exists
        dfes_info = pv_dfes_lookup.get((lsoa, month), {})
        dfes_monthly = dfes_info.get('monthly')
        dfes_cumulative = dfes_info.get('cumulative')

        # Only create row if ACTUAL data OR DFES data exists (locked data contract)
        if actual_install_count is not None or dfes_monthly is not None or dfes_cumulative is not None:
            lsoa_rows.append({
                'period': month,
                'tech_type': 'Solar PV',
                'LSOA21CD': lsoa,
                'DNO': dno,
                'LAEP': laep,
                'actual_install_count': actual_install_count,
                'actual_total_kw': actual_total_kw,
                'actual_cumulative_count': None,
                'actual_cumulative_kw': None,
                'dfes_monthly_benchmark': dfes_monthly,
                'dfes_cumulative_benchmark': dfes_cumulative,
                'dfes_scenario': None,
                'comparison_unit': 'Installed capacity (kW)',
                'EV_Type': None,
                'variance': None,
                'variance_pct': None,
                'comparison_point_type': None,
                'dfes_opening_stock': pv_apr.get(dno, None),
                'dfes_closing_stock': pv_mar.get(dno, None)
            })
            pv_count += 1

print(f"Solar PV LSOA rows: {pv_count:,}")

# EV Charger - calculate cumulative counts per LSOA
evc_lsoa = lsoa_actuals[lsoa_actuals['tech_type'] == 'EV Charger'].copy()
evc_lsoa = evc_lsoa.sort_values('period')
evc_count = len(evc_lsoa)

for lsoa in evc_lsoa['LSOA21CD'].unique():
    lsoa_evc = evc_lsoa[evc_lsoa['LSOA21CD'] == lsoa].sort_values('period')
    dno = lsoa_evc.iloc[0]['DNO'] if len(lsoa_evc) > 0 else 'Unknown'
    laep = lsoa_evc.iloc[0]['LAEP'] if len(lsoa_evc) > 0 else None

    cumul_count = 0
    cumul_kw = 0

    for _, row in lsoa_evc.iterrows():
        cumul_count += (row['install_count'] if pd.notna(row['install_count']) else 0)
        cumul_kw += (row['total_kw'] if pd.notna(row['total_kw']) else 0)

        lsoa_rows.append({
            'period': row['period'],
            'tech_type': 'EV Charger',
            'LSOA21CD': row['LSOA21CD'],
            'DNO': dno,
            'LAEP': laep,
            'actual_install_count': row['install_count'],
            'actual_total_kw': row['total_kw'],
            'actual_cumulative_count': None,
            'actual_cumulative_kw': None,
            'dfes_monthly_benchmark': None,
            'dfes_cumulative_benchmark': None,
            'dfes_scenario': None,
            'comparison_unit': 'Number of charger installations',
            'EV_Type': None,
            'variance': None,
            'variance_pct': None,
            'comparison_point_type': None,
            'dfes_opening_stock': None,
            'dfes_closing_stock': None
        })
print(f"EV Charger LSOA rows: {evc_count:,}")

# ============================================================================
# STEP 2: EV VEHICLES (LSOA-level actual + forecast join)
# ============================================================================
print("\n2. PROCESSING EV VEHICLES (LSOA-level comparison)")
print("-" * 100)

# Load EV actuals (LSOA level)
ev_lsoa_actuals = lsoa_actuals[lsoa_actuals['tech_type'] == 'EV'].copy()
print(f"EV LSOA actuals: {len(ev_lsoa_actuals):,} rows")

# Map DFES vehicle_type to BEV/PHEV
def map_vehicle_type_to_ev_class(vehicle_type_str):
    """Map DFES vehicle types to BEV/PHEV classification"""
    if pd.isna(vehicle_type_str):
        return None
    v = str(vehicle_type_str).upper()
    if '(BEV)' in v:
        return 'BEV'
    elif '(PHEV)' in v:
        return 'PHEV'
    return None

ev_forecast_mapped = ev_forecast.copy()
ev_forecast_mapped['EV_Type'] = ev_forecast_mapped['vehicle_type'].apply(map_vehicle_type_to_ev_class)
ev_forecast_mapped = ev_forecast_mapped[ev_forecast_mapped['EV_Type'].notna()].copy()

print(f"DFES EV forecast (mapped): {len(ev_forecast_mapped):,} rows")

# Filter DFES forecast to quarter-end periods only (to match actual observations)
quarter_end_months = ['2025-06', '2025-09', '2025-12', '2026-03']
ev_forecast_quarterly = ev_forecast_mapped[ev_forecast_mapped['period'].isin(quarter_end_months)].copy()

# Aggregate DFES forecast to LSOA level by EV_Type
ev_forecast_lsoa = ev_forecast_quarterly.groupby(['period', 'LSOA21CD', 'DNO', 'EV_Type'], as_index=False).agg(
    forecast_value=('forecast_value', 'sum')
).rename(columns={'forecast_value': 'dfes_monthly_benchmark'})

print(f"DFES EV forecast (quarterly only): {len(ev_forecast_quarterly):,} rows")
print(f"DFES EV forecast (LSOA-aggregated): {len(ev_forecast_lsoa):,} rows")

# Join actual + forecast on (period, LSOA21CD, EV_Type)
ev_comparison = pd.merge(
    ev_lsoa_actuals,
    ev_forecast_lsoa,
    on=['period', 'LSOA21CD', 'DNO', 'EV_Type'],
    how='outer'
)

# Handle missing values and calculate variance
ev_comparison['actual_install_count'] = ev_comparison['install_count'].fillna(0)
ev_comparison['dfes_monthly_benchmark'] = ev_comparison['dfes_monthly_benchmark'].fillna(0)

# Calculate variance only when both actual and forecast are present
ev_comparison['variance'] = np.nan
ev_comparison['variance_pct'] = np.nan

for idx, row in ev_comparison.iterrows():
    actual = row['actual_install_count']
    forecast = row['dfes_monthly_benchmark']

    if not pd.isna(actual) and not pd.isna(forecast) and forecast != 0:
        variance = actual - forecast
        variance_pct = 100 * variance / forecast
        ev_comparison.at[idx, 'variance'] = variance
        ev_comparison.at[idx, 'variance_pct'] = variance_pct

# Load EV DFES forecast for opening/closing stock
ev_forecast_dno = pd.read_csv(os.path.join(OUTPUT_DIR, "dfes_ev_forecast_high.csv"))
ev_apr = ev_forecast_dno[ev_forecast_dno['period'] == '2025-04'].groupby('DNO')['forecast_value'].sum().to_dict()
ev_mar = ev_forecast_dno[ev_forecast_dno['period'] == '2026-03'].groupby('DNO')['forecast_value'].sum().to_dict()

# Build EV rows
for _, row in ev_comparison.iterrows():
    lsoa_rows.append({
        'period': row['period'],
        'tech_type': 'EV',
        'LSOA21CD': row['LSOA21CD'],
        'DNO': row['DNO'],
        'LAEP': row['LAEP'],
        'actual_install_count': row['actual_install_count'],
        'actual_total_kw': row['total_kw'] if 'total_kw' in row and pd.notna(row['total_kw']) else 0.0,
        'actual_cumulative_count': row['actual_install_count'],
        'actual_cumulative_kw': row['total_kw'] if 'total_kw' in row and pd.notna(row['total_kw']) else 0.0,
        'dfes_monthly_benchmark': row['dfes_monthly_benchmark'],
        'dfes_cumulative_benchmark': row['dfes_monthly_benchmark'],
        'dfes_scenario': 'HolisticTransition',
        'comparison_unit': 'Vehicle stock',
        'EV_Type': row['EV_Type'],
        'variance': row['variance'],
        'variance_pct': row['variance_pct'],
        'comparison_point_type': 'Observation',
        'dfes_opening_stock': ev_apr.get(row['DNO'], None),
        'dfes_closing_stock': ev_mar.get(row['DNO'], None)
    })

print(f"EV LSOA comparison rows added: {len([r for r in lsoa_rows if r['tech_type'] == 'EV']):,}")

# Add EV March 2025 DFES baseline rows
print("Adding EV March 2025 DFES baseline rows...")
ev_april_data = ev_forecast_mapped[ev_forecast_mapped['period'] == '2025-04'].copy()
ev_april_by_lsoa_type = ev_april_data.groupby(['LSOA21CD', 'EV_Type'])['forecast_value'].sum().to_dict()

baseline_count = 0
for lsoa in canonical_lsoas:
    for ev_type in ['BEV', 'PHEV']:
        baseline_val = ev_april_by_lsoa_type.get((lsoa, ev_type))
        if baseline_val is not None:
            dno = lsoa_to_dno.get(lsoa, 'Unknown')
            lsoa_rows.append({
                'period': '2025-03',
                'tech_type': 'EV',
                'LSOA21CD': lsoa,
                'DNO': dno,
                'LAEP': None,
                'actual_install_count': None,
                'actual_total_kw': 0.0,
                'actual_cumulative_count': None,
                'actual_cumulative_kw': None,
                'dfes_monthly_benchmark': baseline_val,
                'dfes_cumulative_benchmark': baseline_val,
                'dfes_scenario': 'HolisticTransition',
                'comparison_unit': 'Vehicle stock',
                'EV_Type': ev_type,
                'variance': None,
                'variance_pct': None,
                'comparison_point_type': 'DFES baseline',
                'dfes_opening_stock': ev_apr.get(dno, None),
                'dfes_closing_stock': ev_mar.get(dno, None)
            })
            baseline_count += 1

print(f"EV baseline rows added: {baseline_count:,} rows")

# ============================================================================
# STEP 3: BUILD OUTPUT DATAFRAME
# ============================================================================
print("\n3. BUILDING OUTPUT DATAFRAME")
print("-" * 100)

df_lsoa_comparison = pd.DataFrame(lsoa_rows)

print(f"Total LSOA comparison rows: {len(df_lsoa_comparison):,}")
print(f"Technologies: {sorted(df_lsoa_comparison['tech_type'].unique())}")
print(f"Unique LSOAs: {df_lsoa_comparison['LSOA21CD'].nunique():,}")

# ============================================================================
# STEP 4: EV COMPLETENESS QA
# ============================================================================
print("\n4. EV COMPLETENESS QA")
print("-" * 100)

ev_lsoa_data = df_lsoa_comparison[df_lsoa_comparison['tech_type'] == 'EV']

# Expected: 4 observation periods × 11,023 LSOAs × 2 EV types + 1 March 2025 baseline
# Baseline period includes 2 EV types but only for LSOAs with LAEP mapping (~11,023 LSOAs)
# However, baseline calculation resulted in 22,046 rows (approximately 2 × 11,023)
expected_quarterly_rows = 4 * 11023 * 2
expected_baseline_rows = 22046  # March 2025 DFES baseline for all LSOAs × 2 EV types
expected_ev_rows = expected_quarterly_rows + expected_baseline_rows

actual_ev_rows = len(ev_lsoa_data)

print(f"Expected quarterly rows (4 periods): {expected_quarterly_rows:,}")
print(f"Expected baseline rows (Mar 2025): {expected_baseline_rows:,}")
print(f"Expected total EV rows: {expected_ev_rows:,}")
print(f"Actual EV rows: {actual_ev_rows:,}")
print(f"Match: {expected_ev_rows == actual_ev_rows}")

# Verify periods: 4 observations + 1 baseline
ev_periods = sorted(ev_lsoa_data['period'].unique())
expected_periods = ['2025-03', '2025-06', '2025-09', '2025-12', '2026-03']
print(f"\nEV periods: {ev_periods}")
print(f"Expected: {expected_periods}")
print(f"Match: {ev_periods == expected_periods}")

# Check for duplicates
duplicates = ev_lsoa_data.duplicated(subset=['period', 'LSOA21CD', 'EV_Type']).sum()
print(f"\nDuplicate (period × LSOA × EV_Type) rows: {duplicates}")

# Check for nulls in key fields
null_lsoa = ev_lsoa_data['LSOA21CD'].isna().sum()
null_dno = ev_lsoa_data['DNO'].isna().sum()
null_actual = ev_lsoa_data['actual_install_count'].isna().sum()

print(f"\nNull checks:")
print(f"  LSOA21CD: {null_lsoa}")
print(f"  DNO: {null_dno}")
print(f"  actual_install_count: {null_actual}")

# Check DNO coverage
dno_values = sorted(ev_lsoa_data['DNO'].unique())
print(f"\nDNO coverage: {dno_values}")
print(f"Expected: ['EPN', 'LPN', 'SPN']")

# ============================================================================
# STEP 5: LSOA → DNO RECONCILIATION (Locked Controls)
# ============================================================================
print("\n5. LSOA to DNO RECONCILIATION (March 2026)")
print("-" * 100)

march_ev = ev_lsoa_data[ev_lsoa_data['period'] == '2026-03']

print("\nActual reconciliation:")
print(f"{'DNO':<6} {'BEV':>12} {'PHEV':>12} {'Total':>12}")
print("-" * 45)

for dno in ['EPN', 'LPN', 'SPN']:
    dno_bev = int(march_ev[(march_ev['DNO'] == dno) & (march_ev['EV_Type'] == 'BEV')]['actual_install_count'].sum())
    dno_phev = int(march_ev[(march_ev['DNO'] == dno) & (march_ev['EV_Type'] == 'PHEV')]['actual_install_count'].sum())
    dno_total = dno_bev + dno_phev
    print(f"{dno:<6} {dno_bev:>12,} {dno_phev:>12,} {dno_total:>12,}")

ukpn_bev = int(march_ev[march_ev['EV_Type'] == 'BEV']['actual_install_count'].sum())
ukpn_phev = int(march_ev[march_ev['EV_Type'] == 'PHEV']['actual_install_count'].sum())
ukpn_total = ukpn_bev + ukpn_phev

print("-" * 45)
print(f"{'UKPN':<6} {ukpn_bev:>12,} {ukpn_phev:>12,} {ukpn_total:>12,}")

print(f"\nExpected UKPN Actual: BEV=598,878, PHEV=310,086, Total=908,964")
print(f"Matches: BEV={ukpn_bev == 598878}, PHEV={ukpn_phev == 310086}, Total={ukpn_total == 908964}")

print("\nForecast reconciliation:")
print(f"{'DNO':<6} {'BEV':>12} {'PHEV':>12} {'Total':>12}")
print("-" * 45)

for dno in ['EPN', 'LPN', 'SPN']:
    dno_bev = int(march_ev[(march_ev['DNO'] == dno) & (march_ev['EV_Type'] == 'BEV')]['dfes_monthly_benchmark'].sum())
    dno_phev = int(march_ev[(march_ev['DNO'] == dno) & (march_ev['EV_Type'] == 'PHEV')]['dfes_monthly_benchmark'].sum())
    dno_total = dno_bev + dno_phev
    print(f"{dno:<6} {dno_bev:>12,} {dno_phev:>12,} {dno_total:>12,}")

ukpn_fc_bev = int(march_ev[march_ev['EV_Type'] == 'BEV']['dfes_monthly_benchmark'].sum())
ukpn_fc_phev = int(march_ev[march_ev['EV_Type'] == 'PHEV']['dfes_monthly_benchmark'].sum())
ukpn_fc_total = ukpn_fc_bev + ukpn_fc_phev

print("-" * 45)
print(f"{'UKPN':<6} {ukpn_fc_bev:>12,} {ukpn_fc_phev:>12,} {ukpn_fc_total:>12,}")

print(f"\nExpected UKPN Forecast: BEV=669,095, PHEV=299,268, Total=968,363")
print(f"Matches: BEV={ukpn_fc_bev == 669095}, PHEV={ukpn_fc_phev == 299268}, Total={ukpn_fc_total == 968363}")

# ============================================================================
# STEP 6: ALL FOUR QUARTERS RECONCILIATION
# ============================================================================
print("\n6. FOUR-QUARTER RECONCILIATION (UKPN Total)")
print("-" * 100)

print(f"{'Period':<12} {'Actual BEV':>12} {'PHEV':>12} {'Total':>12} {'Forecast':>12} {'Variance':>12}")
print("-" * 75)

for period in ['2025-06', '2025-09', '2025-12', '2026-03']:
    period_ev = ev_lsoa_data[ev_lsoa_data['period'] == period]

    act_bev = int(period_ev[period_ev['EV_Type'] == 'BEV']['actual_install_count'].sum())
    act_phev = int(period_ev[period_ev['EV_Type'] == 'PHEV']['actual_install_count'].sum())
    act_total = act_bev + act_phev

    fc_bev = int(period_ev[period_ev['EV_Type'] == 'BEV']['dfes_monthly_benchmark'].sum())
    fc_phev = int(period_ev[period_ev['EV_Type'] == 'PHEV']['dfes_monthly_benchmark'].sum())
    fc_total = fc_bev + fc_phev

    variance = act_total - fc_total

    print(f"{period:<12} {act_bev:>6,}/{act_phev:>5,} {act_total:>12,} {fc_total:>12,} {variance:>12,}")

# ============================================================================
# STEP 7: OTHER TECHNOLOGIES REGRESSION
# ============================================================================
print("\n7. OTHER TECHNOLOGIES REGRESSION")
print("-" * 100)

for tech in ['Heat Pump', 'Solar PV', 'EV Charger']:
    tech_data = df_lsoa_comparison[df_lsoa_comparison['tech_type'] == tech]
    row_count = len(tech_data)
    unique_lsoas = tech_data['LSOA21CD'].nunique()
    print(f"\n{tech}:")
    print(f"  Total rows: {row_count:,}")
    print(f"  Unique LSOAs: {unique_lsoas:,}")
    print(f"  Forecast fields null: {tech_data['dfes_monthly_benchmark'].isna().all()}")

# ============================================================================
# STEP 8: FILE SIZE & LOADING STRATEGY
# ============================================================================
print("\n8. FILE SIZE & FRONTEND LOADING STRATEGY")
print("-" * 100)

file_size_bytes = df_lsoa_comparison.memory_usage(deep=True).sum()
file_size_mb = file_size_bytes / (1024 * 1024)

print(f"In-memory size: {file_size_mb:.2f} MB")
print(f"Total rows: {len(df_lsoa_comparison):,}")

# Estimate CSV size
csv_size_estimate = len(df_lsoa_comparison) * 150 / 1024 / 1024
print(f"Estimated CSV size: {csv_size_estimate:.2f} MB")

tech_breakdown = df_lsoa_comparison.groupby('tech_type').size()
print(f"\nRows by technology:")
for tech in sorted(tech_breakdown.index):
    count = tech_breakdown[tech]
    print(f"  {tech}: {count:,}")

print("\nRecommendation:")
if csv_size_estimate < 10:
    print("  Single file loading is practical for browser (< 10 MB)")
else:
    print("  Consider technology-specific files or DNO partitioning")

# ============================================================================
# STEP 9: LAEP FIELD FINDINGS
# ============================================================================
print("\n9. LAEP FIELD FINDINGS")
print("-" * 100)

ev_with_laep = df_lsoa_comparison[(df_lsoa_comparison['tech_type'] == 'EV') & (df_lsoa_comparison['LAEP'].notna())]
print(f"EV rows with LAEP: {len(ev_with_laep):,} of {len(df_lsoa_comparison[df_lsoa_comparison['tech_type'] == 'EV']):,}")

unique_laeps = sorted(df_lsoa_comparison[df_lsoa_comparison['LAEP'].notna()]['LAEP'].unique())
print(f"\nUnique LAEP values: {len(unique_laeps)}")
print("Sample LAEP values:")
for laep in unique_laeps[:10]:
    print(f"  {laep}")

# ============================================================================
# STEP 10: WRITE OUTPUT
# ============================================================================
print("\n10. WRITING OUTPUT")
print("-" * 100)

output_path = os.path.join(OUTPUT_DIR, "dashboard_comparison_lsoa.csv")
os.makedirs(os.path.dirname(output_path), exist_ok=True)
df_lsoa_comparison.to_csv(output_path, index=False)
print(f"[OK] Written to {output_path}")
print(f"     Rows: {len(df_lsoa_comparison):,}")
print(f"     Size: ~{csv_size_estimate:.2f} MB")

print("\n" + "=" * 100)
print("STAGE 2F LSOA OUTPUT COMPLETE")
print("=" * 100)
