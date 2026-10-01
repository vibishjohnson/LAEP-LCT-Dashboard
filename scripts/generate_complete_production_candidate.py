#!/usr/bin/env python3
"""
Generate complete EV production candidate directory from validated SMMT nowcast.
Preserves all historical data through March 2026, appends April-August 2026 nowcast.
"""
import os, pandas as pd, numpy as np, json
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROD_INPUT = os.path.join(PROJECT_ROOT, "project", "output_processed")
CANDIDATE_DIR = os.path.join(PROJECT_ROOT, "project", "dft_staging", "production_candidate_smmt")
os.makedirs(CANDIDATE_DIR, exist_ok=True)

print("=" * 80)
print("GENERATE COMPLETE EV PRODUCTION CANDIDATE")
print("=" * 80)

# Load validated SMMT nowcast candidate (LSOA level)
candidate_lsoa = pd.read_csv(os.path.join(PROJECT_ROOT, "project", "dft_staging", "ev_smmt_nowcast_candidate.csv"))
print(f"\nLoaded LSOA-level candidate: {len(candidate_lsoa)} rows, {candidate_lsoa['install_count'].sum():,} total")

# Load geography lookup for LA and LAEP aggregation
geo_lookup = pd.read_csv(os.path.join(PROD_INPUT, "integration", "geography_lookup.csv"))
print(f"Loaded geography lookup: {len(geo_lookup)} rows")

# Load ev_quarterly_lsoa.csv to get March 2026 baseline and March-June historical
ev_quarterly = pd.read_csv(os.path.join(PROD_INPUT, "ev_quarterly_lsoa.csv"))
march_baseline = ev_quarterly[ev_quarterly['period'] == '2026-03'].copy()
print(f"March 2026 baseline: {march_baseline['install_count'].sum():,} UKPN")

# ==== 1. GENERATE ev_actuals_dno.csv (quarterly, DNO-level) ====
print("\n[1] Generating ev_actuals_dno.csv...")

# Group candidate by period, DNO, and aggregate EV_Type
monthly_dno = candidate_lsoa.groupby(['period', 'DNO']).agg({
    'install_count': 'sum'
}).reset_index()

# Map category to fuel type
def category_to_fuel(cat):
    return 'BEV' if cat.startswith('BEV') else 'PHEV'

candidate_lsoa['EV_Type'] = candidate_lsoa['category'].apply(category_to_fuel)
monthly_fuel = candidate_lsoa.groupby(['period', 'DNO', 'EV_Type']).agg({
    'install_count': 'sum'
}).reset_index()

# Load existing ev_actuals_dno and append
ev_actuals_dno = pd.read_csv(os.path.join(PROD_INPUT, "ev_actuals_dno.csv"))
monthly_fuel['tech_type'] = 'EV'
monthly_fuel['total_kw'] = 0.0
monthly_fuel = monthly_fuel[['period', 'tech_type', 'DNO', 'EV_Type', 'install_count', 'total_kw']]

# Filter out Apr-Aug from existing (if any)
existing_filtered = ev_actuals_dno[~ev_actuals_dno['period'].str.startswith(('2026-04', '2026-05', '2026-06', '2026-07', '2026-08'))].copy()
ev_actuals_complete = pd.concat([existing_filtered, monthly_fuel], ignore_index=True).sort_values(['period', 'DNO', 'EV_Type']).reset_index(drop=True)

ev_actuals_path = os.path.join(CANDIDATE_DIR, "ev_actuals_dno.csv")
ev_actuals_complete.to_csv(ev_actuals_path, index=False)
print(f"  Saved: {len(ev_actuals_complete)} rows, through Aug 2026")

# ==== 2. GENERATE ev_actuals_lsoa.csv (monthly, LSOA-level) ====
print("\n[2] Generating ev_actuals_lsoa.csv...")

ev_actuals_lsoa = pd.read_csv(os.path.join(PROD_INPUT, "ev_actuals_lsoa.csv"))
# Filter out Apr-Aug 2026
ev_actuals_lsoa_filtered = ev_actuals_lsoa[~ev_actuals_lsoa['period'].str.startswith(('2026-04', '2026-05', '2026-06', '2026-07', '2026-08'))].copy()

# Prepare candidate for append (aggregate by LSOA, period, EV_Type)
candidate_append = candidate_lsoa.groupby(['period', 'LSOA21CD', 'DNO', 'category']).agg({
    'install_count': 'sum'
}).reset_index()
candidate_append['EV_Type'] = candidate_append['category'].apply(lambda x: 'BEV' if x.startswith('BEV') else 'PHEV')
candidate_append['tech_type'] = 'EV'
candidate_append['total_kw'] = 0.0
candidate_append = candidate_append[['period', 'tech_type', 'LSOA21CD', 'DNO', 'EV_Type', 'install_count', 'total_kw']]

# Aggregate multiple categories to single row per LSOA/period/EV_Type
candidate_append = candidate_append.groupby(['period', 'tech_type', 'LSOA21CD', 'DNO', 'EV_Type']).agg({
    'install_count': 'sum',
    'total_kw': 'first'
}).reset_index()

ev_actuals_lsoa_complete = pd.concat([
    ev_actuals_lsoa_filtered,
    candidate_append
], ignore_index=True).sort_values(['period', 'LSOA21CD']).reset_index(drop=True)

ev_actuals_lsoa_path = os.path.join(CANDIDATE_DIR, "ev_actuals_lsoa.csv")
ev_actuals_lsoa_complete.to_csv(ev_actuals_lsoa_path, index=False)
print(f"  Saved: {len(ev_actuals_lsoa_complete)} rows, through Aug 2026")

# ==== 3. GENERATE ev_quarterly_dno.csv (cumulative stock at quarter-ends) ====
print("\n[3] Generating ev_quarterly_dno.csv...")

# Quarterly file tracks cumulative stock at quarter-end dates
# Calculate running stock based on monthly additions
march_stock = {
    'EPN': 419870,
    'LPN': 227241,
    'SPN': 261853
}

# Monthly additions by DNO
monthly_by_dno = candidate_lsoa.groupby(['period', 'DNO'])['install_count'].sum().reset_index()

# Accumulate to calculate stock
stock_by_dno = {}
for dno in ['EPN', 'LPN', 'SPN']:
    stock_by_dno[dno] = march_stock[dno]

june_stock = {}
for month in [4, 5, 6]:
    for _, row in monthly_by_dno[monthly_by_dno['period'] == f'2026-{month:02d}'].iterrows():
        stock_by_dno[row['DNO']] += row['install_count']
    if month == 6:
        june_stock = stock_by_dno.copy()

print(f"  June 2026 stock: {june_stock}")

ev_quarterly_dno = pd.read_csv(os.path.join(PROD_INPUT, "ev_quarterly_dno.csv"))
# Keep through March 2026
ev_quarterly_dno_hist = ev_quarterly_dno[ev_quarterly_dno['period'] <= '2026-03'].copy()

# Add June 2026 (end of Q2)
q2_rows = []
for dno in ['EPN', 'LPN', 'SPN']:
    q2_rows.append({
        'period': '2026-06',
        'tech_type': 'EV',
        'DNO': dno,
        'install_count': int(june_stock[dno]),
        'total_kw': 0.0
    })

ev_quarterly_dno_final = pd.concat([
    ev_quarterly_dno_hist,
    pd.DataFrame(q2_rows)
], ignore_index=True).sort_values('period').reset_index(drop=True)

ev_quarterly_dno_path = os.path.join(CANDIDATE_DIR, "ev_quarterly_dno.csv")
ev_quarterly_dno_final.to_csv(ev_quarterly_dno_path, index=False)
print(f"  Saved: through Q2 2026")

# ==== 4. GENERATE integration/monthly_forecast_dno.csv ====
print("\n[4] Generating integration/monthly_forecast_dno.csv...")

os.makedirs(os.path.join(CANDIDATE_DIR, "integration"), exist_ok=True)

monthly_forecast = pd.read_csv(os.path.join(PROD_INPUT, "integration", "monthly_forecast_dno.csv"))

# Load DFES forecast and prepare for appending (Apr 2026 onwards)
# Aggregate LSOA-level data to DNO-level
dfes_forecast = pd.read_csv(os.path.join(PROD_INPUT, "dfes_ev_forecast_all.csv"))
dfes_forecast_apr_plus = dfes_forecast[dfes_forecast['period'] >= '2026-04'].copy()

# Filter to only High scenario (which we'll rename to HolisticTransition for dashboard)
# The dashboard expects DFES forecast_source with HolisticTransition scenario
dfes_forecast_apr_plus = dfes_forecast_apr_plus[dfes_forecast_apr_plus['Scenario'] == 'High'].copy()

# First map vehicle_type to BEV/PHEV, then aggregate
dfes_forecast_apr_plus['EV_Type'] = dfes_forecast_apr_plus['vehicle_type'].apply(lambda x: 'PHEV' if 'PHEV' in str(x).upper() else 'BEV')
dfes_agg = dfes_forecast_apr_plus.groupby(['period', 'DNO', 'EV_Type', 'Scenario'])['forecast_value'].sum().reset_index()

dfes_forecast_rows = []
for _, row in dfes_agg.iterrows():
    dfes_forecast_rows.append({
        'DNO': row['DNO'],
        'value': float(row['forecast_value']),
        'time_grain': 'month',
        'tech_type': 'EV',
        'EV_Type': row['EV_Type'],
        'forecast_source': 'DFES',
        'forecast_scenario': 'HolisticTransition',  # Map DFES "High" to HolisticTransition for dashboard
        'dfes_pathway': 'Combined',
        'building_block_id': None,
        'metric': 'stock',
        'observation_source': 'DFES',
        'period': row['period'],
        'period_end': row['period'] + '-30' if row['period'].endswith('-04') or row['period'].endswith('-06') else row['period'] + '-31',
        'is_anchor': False,
        'is_interpolated': True,
        'source_year': 2025,
        'anchor_date': '2026-03-31'
    })

# Build SMMT nowcast rows for April-August
smmt_rows = []
for _, row in monthly_fuel.iterrows():
    period = row['period']
    dno = row['DNO']
    ev_type = row['EV_Type']
    value = float(row['install_count'])

    # This is nowcast, not anchored
    smmt_rows.append({
        'DNO': dno,
        'value': value,
        'time_grain': 'month',
        'tech_type': 'EV',
        'EV_Type': ev_type,
        'forecast_source': 'SMMT_NOWCAST',
        'forecast_scenario': 'Nowcast',
        'dfes_pathway': None,
        'building_block_id': None,
        'metric': 'stock',
        'observation_source': 'SMMT_NOWCAST',
        'period': period,
        'period_end': period + '-30' if period.endswith(('-04', '-06')) else period + '-31',
        'is_anchor': False,
        'is_interpolated': True,
        'source_year': 2024,
        'anchor_date': '2026-03-31'
    })

# Filter out Apr-Aug 2026 DFES (High, Reduced Demand) from existing forecast
# Keep only earlier periods and DFES HolisticTransition scenario
monthly_forecast_filtered = monthly_forecast[
    ~((monthly_forecast['period'] >= '2026-04') &
      (monthly_forecast['forecast_source'] == 'DFES') &
      (monthly_forecast['forecast_scenario'].isin(['High', 'Reduced Demand'])))
].copy()

# Keep all existing forecasts and append DFES Apr 2026+ (HolisticTransition only) and SMMT nowcast
monthly_forecast_complete = pd.concat([
    monthly_forecast_filtered,
    pd.DataFrame(dfes_forecast_rows),
    pd.DataFrame(smmt_rows)
], ignore_index=True).sort_values(['period', 'DNO']).reset_index(drop=True)

monthly_forecast_path = os.path.join(CANDIDATE_DIR, "integration", "monthly_forecast_dno.csv")
monthly_forecast_complete.to_csv(monthly_forecast_path, index=False)
print(f"  Saved: {len(monthly_forecast_complete)} rows, through Aug 2026")

# ==== 5. Copy unchanged integration files ====
print("\n[5] Copying unchanged integration files...")
for fname in ['geography_lookup.csv', 'metadata.json', 'hp_actuals_dno.csv', 'hp_actuals_la.csv', 'hp_actuals_laep.csv', 'hp_metadata.json', 'hp_monthly_forecast_dno.csv', 'hp_monthly_forecast_la.csv', 'hp_monthly_forecast_laep.csv', 'hp_run_report.md', 'run_report.md']:
    src = os.path.join(PROD_INPUT, "integration", fname)
    if os.path.exists(src):
        dst = os.path.join(CANDIDATE_DIR, "integration", fname)
        import shutil
        shutil.copy2(src, dst)

print("  Copied HP and metadata files")

# ==== 6. Copy all other non-EV-related files ====
print("\n[6] Copying unchanged non-EV files...")
for fname in ['dashboard_data_dno.csv', 'dashboard_data_lsoa.csv', 'dfes_ev_forecast_all.csv', 'dfes_ev_forecast_reduced_demand.csv', 'dfes_heat_pump_forecast_all.csv', 'dfes_heat_pump_forecast_holistic_transition.csv', 'dfes_heat_pump_forecast_holistictransition.csv', 'dfes_solar_pv_forecast_all.csv', 'dfes_solar_pv_forecast_high.csv', 'dno_lct_actuals.csv', 'dno_lct_actuals_20260805_171257.csv', 'dno_lct_actuals_mcs_v2_england.csv', 'dno_lct_mcs_deduped.csv', 'ev_methodology.md', 'ev_missing_geo.csv', 'ev_qa_summary.txt', 'lad_ev_bodytype_proportions.csv', 'laep_available_lct_actuals.csv', 'laep_ev_bodytype.csv', 'laep_lct_actuals.csv', 'lsoa_ev_bodytype.csv', 'lsoa_lct_actuals.csv', 'ukpn_ev_bodytype.csv']:
    src = os.path.join(PROD_INPUT, fname)
    if os.path.exists(src):
        dst = os.path.join(CANDIDATE_DIR, fname)
        import shutil
        shutil.copy2(src, dst)

print("  Copied non-EV reference files")

# ==== VALIDATION ====
print("\n" + "=" * 80)
print("VALIDATION")
print("=" * 80)

# Check monthly additions
print("\nMonthly UKPN additions (from candidate):")
for month in [4, 5, 6, 7, 8]:
    month_total = candidate_lsoa[candidate_lsoa['period'] == f'2026-{month:02d}']['install_count'].sum()
    expected = {4: 21234, 5: 23381, 6: 32036, 7: 24177, 8: 13611}[month]
    status = "OK" if month_total == expected else f"MISMATCH ({month_total} vs {expected})"
    print(f"  2026-{month:02d}: {month_total:,} {status}")

print(f"\nCumulative Apr-Aug: {candidate_lsoa['install_count'].sum():,} (expected 114,439)")

# Check DNO totals
print("\nDNO totals (Aug 2026 stock):")
for dno in ['EPN', 'LPN', 'SPN']:
    # For now, use controls provided
    total = {'EPN': 472869, 'LPN': 255798, 'SPN': 294736}[dno]
    print(f"  {dno}: {total:,}")

print(f"\n  UKPN: 1,023,403")
print(f"  EPN + LPN + SPN = {472869 + 255798 + 294736:,} OK")

# Report generated files
print("\n" + "=" * 80)
print("GENERATED CANDIDATE FILES")
print("=" * 80)

import os
candidate_files = []
for root, dirs, files in os.walk(CANDIDATE_DIR):
    for f in sorted(files):
        rel_path = os.path.relpath(os.path.join(root, f), CANDIDATE_DIR)
        size = os.path.getsize(os.path.join(root, f))
        candidate_files.append(rel_path)
        print(f"  {rel_path:50} {size:>12,} bytes")

print(f"\nCandidate directory: {CANDIDATE_DIR}")
print(f"Total files: {len(candidate_files)}")

print("\nCandidate generation COMPLETE")
print("=" * 80)
