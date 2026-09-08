#!/usr/bin/env python3
"""
Stage 2F: Dashboard Comparison Data (Actuals vs DFES Benchmarks)
Builds clean DNO-level comparison without replacing actual observations.
"""

import pandas as pd
import numpy as np
import os
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "project", "output_processed")
STAGE_2D_DIR = os.path.join(PROJECT_ROOT, "output")

print("=" * 100)
print("STAGE 2F: DASHBOARD COMPARISON DATA (Actuals vs DFES Benchmarks)")
print("=" * 100)
print(f"Timestamp: {datetime.utcnow().isoformat()}Z")

# ============================================================================
# STEP 0: LOAD INPUTS
# ============================================================================
print("\n0. LOADING INPUTS")
print("-" * 100)

# Stage 2D production data (pre-adapter, contains all sources)
stage_2d = pd.read_csv(os.path.join(STAGE_2D_DIR, "stage_2d_production.csv"), low_memory=False)

# Filter to INCLUDE methodology and date range
stage_2e_actuals = stage_2d[
    (stage_2d['methodology_status'] == 'INCLUDE') &
    (stage_2d['reporting_period'].notna()) &
    (stage_2d['reporting_period'] >= '2025-04') &
    (stage_2d['reporting_period'] <= '2026-03')
].copy()

print(f"Stage 2E actuals: {len(stage_2e_actuals):,} observations")

# Load DFES forecast CSVs
hp_forecast = pd.read_csv(os.path.join(OUTPUT_DIR, "dfes_heat_pump_forecast_holistic_transition.csv"))
pv_forecast = pd.read_csv(os.path.join(OUTPUT_DIR, "dfes_solar_pv_forecast_high.csv"))
ev_forecast = pd.read_csv(os.path.join(OUTPUT_DIR, "dfes_ev_forecast_reduced_demand.csv"))

print(f"DFES Heat Pump forecast: {len(hp_forecast):,} records")
print(f"DFES Solar PV forecast: {len(pv_forecast):,} records")
print(f"DFES EV forecast: {len(ev_forecast):,} records")

# ============================================================================
# STEP 1: DERIVE DFES OPENING/CLOSING STOCKS (Validation Anchors)
# ============================================================================
print("\n1. DERIVING DFES OPENING/CLOSING STOCKS")
print("-" * 100)

# The forecast CSVs are already interpolated monthly, so we extract April and March values
# April = April 2025 (opening month of FY)
# March = March 2026 (closing month of FY)

# Heat Pump
hp_apr = hp_forecast[hp_forecast['period'] == '2025-04'].groupby('DNO')['forecast_value'].sum()
hp_mar = hp_forecast[hp_forecast['period'] == '2026-03'].groupby('DNO')['forecast_value'].sum()
hp_apr_total = hp_apr.sum()
hp_mar_total = hp_mar.sum()

print(f"\nHeat Pump (HolisticTransition):")
print(f"  UKPN Apr 2025 opening: {hp_apr_total:,.0f}")
print(f"  UKPN Mar 2026 closing: {hp_mar_total:,.0f}")
print(f"  UKPN implied FY uptake: {hp_mar_total - hp_apr_total:,.0f}")
for dno in ['EPN', 'LPN', 'SPN']:
    uptake = hp_mar.get(dno, 0) - hp_apr.get(dno, 0)
    print(f"    {dno}: {hp_apr.get(dno, 0):.0f} -> {hp_mar.get(dno, 0):.0f} (+{uptake:.0f})")

# Expected DFES anchors
expected_hp_uptake = 68184
actual_hp_uptake = hp_mar_total - hp_apr_total
print(f"\n  Validation: Expected UKPN uptake = {expected_hp_uptake:,}, Derived = {actual_hp_uptake:,.0f}, Match: {abs(actual_hp_uptake - expected_hp_uptake) < 10}")

# Solar PV
pv_apr = pv_forecast[pv_forecast['period'] == '2025-04'].groupby('DNO')['forecast_value'].sum()
pv_mar = pv_forecast[pv_forecast['period'] == '2026-03'].groupby('DNO')['forecast_value'].sum()
pv_apr_total = pv_apr.sum()
pv_mar_total = pv_mar.sum()

print(f"\nSolar PV (High scenario):")
print(f"  UKPN Apr 2025 opening: {pv_apr_total:,.0f} kW")
print(f"  UKPN Mar 2026 closing: {pv_mar_total:,.0f} kW")
print(f"  UKPN implied FY uptake: {pv_mar_total - pv_apr_total:,.0f} kW")
for dno in ['EPN', 'LPN', 'SPN']:
    uptake = pv_mar.get(dno, 0) - pv_apr.get(dno, 0)
    print(f"    {dno}: {pv_apr.get(dno, 0):,.0f} -> {pv_mar.get(dno, 0):,.0f} (+{uptake:,.0f})")

expected_pv_uptake = 147738
actual_pv_uptake = pv_mar_total - pv_apr_total
print(f"\n  Validation: Expected UKPN uptake = {expected_pv_uptake:,}, Derived = {actual_pv_uptake:,.0f}, Match: {abs(actual_pv_uptake - expected_pv_uptake) < 10}")

# EV (for reference, but not used in comparison)
ev_apr = ev_forecast[ev_forecast['period'] == '2025-04'].groupby('DNO')['forecast_value'].sum()
ev_mar = ev_forecast[ev_forecast['period'] == '2026-03'].groupby('DNO')['forecast_value'].sum()
ev_apr_total = ev_apr.sum()
ev_mar_total = ev_mar.sum()

print(f"\nEV Vehicles (for reference):")
print(f"  UKPN Apr 2025 opening: {ev_apr_total:,.0f}")
print(f"  UKPN Mar 2026 closing: {ev_mar_total:,.0f}")
print(f"  UKPN implied FY uptake: {ev_mar_total - ev_apr_total:,.0f} (NOT used in charger comparison)")

# ============================================================================
# STEP 2: AGGREGATE STAGE 2E ACTUALS BY MONTH/TECH/DNO
# ============================================================================
print("\n2. AGGREGATING STAGE 2E ACTUALS")
print("-" * 100)

# Create tech_type mapping
stage_2e_actuals['tech_type'] = stage_2e_actuals['technology_canonical'].replace({
    'EV Charging': 'EV Charger'
})

# Prepare capacity field selection by source/tech
def select_capacity_for_stage2f(row):
    source = row['source']
    if source == 'MCS':
        return row['capacity_kw']
    elif source == 'LCT_REGISTER':
        return row['methodology_capacity_kw']
    elif source in ['ECR_SMALL', 'ECR_LARGE']:
        return row['capacity_kw']
    elif source == 'ZAPMAP':
        return row['capacity_kw']
    else:
        return None

stage_2e_actuals['capacity_selected'] = stage_2e_actuals.apply(select_capacity_for_stage2f, axis=1)

# Group by period, tech_type, DNO
actuals_agg = stage_2e_actuals.groupby(
    ['reporting_period', 'tech_type', 'licence_area'],
    as_index=False
).agg(
    install_count=('reporting_period', 'count'),
    total_kw=('capacity_selected', 'sum')
).rename(columns={'reporting_period': 'period', 'licence_area': 'DNO'})

actuals_agg['total_kw'] = actuals_agg['total_kw'].fillna(0)

print(f"Aggregated actuals: {len(actuals_agg):,} rows")
print(f"Unique technologies: {sorted(actuals_agg['tech_type'].unique())}")
print(f"Unique DNOs: {sorted(actuals_agg['DNO'].unique())}")

# Validate Stage 2E totals
for tech in ['Heat Pump', 'Solar PV', 'EV Charger']:
    tech_data = actuals_agg[actuals_agg['tech_type'] == tech]
    total_count = tech_data['install_count'].sum()
    total_kw = tech_data['total_kw'].sum()
    print(f"\n{tech}:")
    print(f"  Total observations: {total_count:,}")
    print(f"  Total capacity: {total_kw:,.0f} kW")

# ============================================================================
# STEP 3: BUILD MONTHLY BENCHMARKS
# ============================================================================
print("\n3. BUILDING MONTHLY BENCHMARKS")
print("-" * 100)

months = ['2025-04', '2025-05', '2025-06', '2025-07', '2025-08', '2025-09',
          '2025-10', '2025-11', '2025-12', '2026-01', '2026-02', '2026-03']

comparison_rows = []

# Heat Pump benchmark
for dno in ['EPN', 'LPN', 'SPN']:
    annual_uptake = hp_mar.get(dno, 0) - hp_apr.get(dno, 0)
    monthly_benchmark = annual_uptake / 12

    for month_idx, month in enumerate(months):
        # Cumulative benchmark = monthly x (month_idx + 1)
        cumulative_benchmark = monthly_benchmark * (month_idx + 1)

        # Get actual for this month/tech/DNO
        actual_data = actuals_agg[
            (actuals_agg['period'] == month) &
            (actuals_agg['tech_type'] == 'Heat Pump') &
            (actuals_agg['DNO'] == dno)
        ]

        actual_count = actual_data['install_count'].values[0] if len(actual_data) > 0 else 0
        actual_kw = actual_data['total_kw'].values[0] if len(actual_data) > 0 else 0

        # Calculate cumulative actuals
        prev_months = actuals_agg[
            (actuals_agg['period'] < month) &
            (actuals_agg['tech_type'] == 'Heat Pump') &
            (actuals_agg['DNO'] == dno)
        ]
        cumulative_count = prev_months['install_count'].sum() + actual_count
        cumulative_kw = prev_months['total_kw'].sum() + actual_kw

        comparison_rows.append({
            'period': month,
            'tech_type': 'Heat Pump',
            'DNO': dno,
            'actual_install_count': actual_count,
            'actual_total_kw': actual_kw,
            'actual_cumulative_count': cumulative_count,
            'actual_cumulative_kw': cumulative_kw,
            'dfes_monthly_benchmark': monthly_benchmark,
            'dfes_cumulative_benchmark': cumulative_benchmark,
            'dfes_scenario': 'HolisticTransition',
            'comparison_unit': 'Number of installations',
            'capacity_known_count': 0.0,
            'capacity_unknown_count': 0.0,
            'capacity_coverage_pct': 0.0
        })

print(f"Heat Pump benchmarks added: {len([r for r in comparison_rows if r['tech_type'] == 'Heat Pump']):,} rows")

# Solar PV benchmark
for dno in ['EPN', 'LPN', 'SPN']:
    annual_capacity_uptake = pv_mar.get(dno, 0) - pv_apr.get(dno, 0)
    monthly_benchmark = annual_capacity_uptake / 12

    for month_idx, month in enumerate(months):
        cumulative_benchmark = monthly_benchmark * (month_idx + 1)

        actual_data = actuals_agg[
            (actuals_agg['period'] == month) &
            (actuals_agg['tech_type'] == 'Solar PV') &
            (actuals_agg['DNO'] == dno)
        ]

        actual_count = actual_data['install_count'].values[0] if len(actual_data) > 0 else 0
        actual_kw = actual_data['total_kw'].values[0] if len(actual_data) > 0 else 0

        # Calculate cumulative
        prev_months = actuals_agg[
            (actuals_agg['period'] < month) &
            (actuals_agg['tech_type'] == 'Solar PV') &
            (actuals_agg['DNO'] == dno)
        ]
        cumulative_count = prev_months['install_count'].sum() + actual_count
        cumulative_kw = prev_months['total_kw'].sum() + actual_kw

        # Capacity coverage for Solar
        solar_obs = stage_2e_actuals[
            (stage_2e_actuals['reporting_period'] == month) &
            (stage_2e_actuals['tech_type'] == 'Solar PV') &
            (stage_2e_actuals['licence_area'] == dno)
        ]
        capacity_known = len(solar_obs[solar_obs['capacity_selected'].notna()])
        capacity_unknown = len(solar_obs[solar_obs['capacity_selected'].isna()])
        capacity_coverage = 100 * capacity_known / len(solar_obs) if len(solar_obs) > 0 else 0

        comparison_rows.append({
            'period': month,
            'tech_type': 'Solar PV',
            'DNO': dno,
            'actual_install_count': actual_count,
            'actual_total_kw': actual_kw,
            'actual_cumulative_count': cumulative_count,
            'actual_cumulative_kw': cumulative_kw,
            'dfes_monthly_benchmark': monthly_benchmark,
            'dfes_cumulative_benchmark': cumulative_benchmark,
            'dfes_scenario': 'High (Holistic Transition)',
            'comparison_unit': 'Installed capacity (kW)',
            'capacity_known_count': capacity_known,
            'capacity_unknown_count': capacity_unknown,
            'capacity_coverage_pct': capacity_coverage
        })

print(f"Solar PV benchmarks added: {len([r for r in comparison_rows if r['tech_type'] == 'Solar PV']):,} rows")

# EV Charger (no DFES comparison)
for dno in ['EPN', 'LPN', 'SPN']:
    for month_idx, month in enumerate(months):
        actual_data = actuals_agg[
            (actuals_agg['period'] == month) &
            (actuals_agg['tech_type'] == 'EV Charger') &
            (actuals_agg['DNO'] == dno)
        ]

        actual_count = actual_data['install_count'].values[0] if len(actual_data) > 0 else 0
        actual_kw = actual_data['total_kw'].values[0] if len(actual_data) > 0 else 0

        # Calculate cumulative
        prev_months = actuals_agg[
            (actuals_agg['period'] < month) &
            (actuals_agg['tech_type'] == 'EV Charger') &
            (actuals_agg['DNO'] == dno)
        ]
        cumulative_count = prev_months['install_count'].sum() + actual_count
        cumulative_kw = prev_months['total_kw'].sum() + actual_kw

        comparison_rows.append({
            'period': month,
            'tech_type': 'EV Charger',
            'DNO': dno,
            'actual_install_count': actual_count,
            'actual_total_kw': actual_kw,
            'actual_cumulative_count': cumulative_count,
            'actual_cumulative_kw': cumulative_kw,
            'dfes_monthly_benchmark': None,
            'dfes_cumulative_benchmark': None,
            'dfes_scenario': None,
            'comparison_unit': 'Number of charger installations',
            'capacity_known_count': None,
            'capacity_unknown_count': None,
            'capacity_coverage_pct': None
        })

print(f"EV Charger records added: {len([r for r in comparison_rows if r['tech_type'] == 'EV Charger']):,} rows")

# ============================================================================
# STEP 4: BUILD OUTPUT DATAFRAME
# ============================================================================
print("\n4. BUILDING OUTPUT DATAFRAME")
print("-" * 100)

df_comparison = pd.DataFrame(comparison_rows)

print(f"Output shape: {df_comparison.shape}")
print(f"Total rows: {len(df_comparison):,}")
print(f"Periods: {sorted(df_comparison['period'].unique())}")
print(f"Technologies: {sorted(df_comparison['tech_type'].unique())}")
print(f"DNOs: {sorted(df_comparison['DNO'].unique())}")

# Verify schema
print(f"\nColumns: {list(df_comparison.columns)}")

# ============================================================================
# STEP 5: VALIDATION TESTS
# ============================================================================
print("\n5. VALIDATION TESTS")
print("-" * 100)

# Test A: Sum monthly actual == Stage 2E totals
print("\nTest A: Monthly actual summation")
for tech in ['Heat Pump', 'Solar PV', 'EV Charger']:
    monthly_total = df_comparison[df_comparison['tech_type'] == tech]['actual_install_count'].sum()
    expected = actuals_agg[actuals_agg['tech_type'] == tech]['install_count'].sum()
    match = monthly_total == expected
    print(f"  {tech}: Monthly sum = {monthly_total:,}, Expected = {expected:,}, Match: {match}")

# Test B: March cumulative benchmark matches annual uptake
print("\nTest B: March cumulative == annual uptake")
for tech in ['Heat Pump', 'Solar PV']:
    march_data = df_comparison[(df_comparison['period'] == '2026-03') & (df_comparison['tech_type'] == tech)]
    march_benchmarks = march_data['dfes_cumulative_benchmark'].sum()
    if tech == 'Heat Pump':
        expected = 68184
    else:
        expected = 147738
    match = abs(march_benchmarks - expected) < 10
    print(f"  {tech}: March cumulative = {march_benchmarks:,.0f}, Expected = {expected:,}, Match: {match}")

# Test C: EV Charger has null DFES
print("\nTest C: EV Charger DFES fields are null")
ev_dfes_null = df_comparison[
    (df_comparison['tech_type'] == 'EV Charger') &
    (df_comparison['dfes_monthly_benchmark'].notna())
]
if len(ev_dfes_null) == 0:
    print("  PASSED")
else:
    print(f"  FAIL: {len(ev_dfes_null)} rows have non-null DFES values")

# Test D: Exactly 12 periods per tech/DNO
print("\nTest D: Exactly 12 periods per tech/DNO")
periods_ok = all(
    len(df_comparison[(df_comparison['tech_type'] == tech) & (df_comparison['DNO'] == dno)]) == 12
    for tech in df_comparison['tech_type'].unique()
    for dno in df_comparison['DNO'].unique()
)
print(f"  {'PASSED' if periods_ok else 'FAILED'}")

# ============================================================================
# STEP 6: WRITE OUTPUT
# ============================================================================
print("\n6. WRITING OUTPUT")
print("-" * 100)

output_path = os.path.join(OUTPUT_DIR, "dashboard_comparison_dno_monthly.csv")
os.makedirs(os.path.dirname(output_path), exist_ok=True)
df_comparison.to_csv(output_path, index=False)
print(f"[OK] Written to {output_path}")

# ============================================================================
# STEP 7: DETERMINISM CHECK (prepare for second run)
# ============================================================================
print("\n7. DETERMINISM CHECKSUM")
print("-" * 100)

import hashlib
with open(output_path, 'rb') as f:
    file_hash = hashlib.sha256(f.read()).hexdigest()
print(f"SHA-256: {file_hash}")

print("\n" + "=" * 100)
print("STAGE 2F COMPLETE")
print("=" * 100)
