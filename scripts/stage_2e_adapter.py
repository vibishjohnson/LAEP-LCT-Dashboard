#!/usr/bin/env python3
"""
Stage 2E: App-Facing Production Adapter
Converts Stage 2D production data to DNO and LSOA-level aggregations
"""

import pandas as pd
import numpy as np
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_PROCESSED = os.path.join(PROJECT_ROOT, "project", "output_processed")

# Load Stage 2D production
prod = pd.read_csv(os.path.join(PROJECT_ROOT, "output", "stage_2d_production.csv"), low_memory=False)

# Filter: methodology_status == "INCLUDE" AND reporting_period is not null
# AND date range Apr 2025 - Mar 2026 (same as Pipeline 1)
monthly_eligible = prod[
    (prod["methodology_status"] == "INCLUDE") &
    (prod["reporting_period"].notna()) &
    (prod["reporting_period"] >= "2025-04") &
    (prod["reporting_period"] <= "2026-03")
].copy()

print(f"Stage 2E Input (before date filter): {len(monthly_eligible):,} monthly-eligible observations")
print(f"  Heat Pump: {len(monthly_eligible[monthly_eligible['technology_canonical'] == 'Heat Pump']):,} (expected 76,233)")
print(f"  Solar PV: {len(monthly_eligible[monthly_eligible['technology_canonical'] == 'Solar PV']):,} (expected 376,316)")
print(f"  EV Charging: {len(monthly_eligible[monthly_eligible['technology_canonical'] == 'EV Charging']):,} (expected 171,553)")

# Rename technology for app output (EV Charging → EV Charger)
monthly_eligible["tech_type"] = monthly_eligible["technology_canonical"].replace({
    "EV Charging": "EV Charger"
})

# Select capacity by source + technology
def select_capacity(row):
    """Select appropriate capacity field based on source and technology"""
    source = row["source"]

    if source == "MCS":
        return row["capacity_kw"]
    elif source == "LCT_REGISTER":
        return row["methodology_capacity_kw"]
    elif source in ["ECR_SMALL", "ECR_LARGE"]:
        return row["capacity_kw"]
    elif source == "ZAPMAP":
        return row["capacity_kw"]
    else:
        return None

monthly_eligible["capacity_selected"] = monthly_eligible.apply(select_capacity, axis=1)

# Extract period from reporting_period
monthly_eligible["period"] = monthly_eligible["reporting_period"].astype(str)

# Use licence_area as DNO
monthly_eligible["DNO"] = monthly_eligible["licence_area"]

# ============================================================================
# DNO-level aggregation
# ============================================================================
print("Aggregating to DNO level...")

dno_agg = monthly_eligible.groupby(['period', 'tech_type', 'DNO'], as_index=False).agg(
    install_count=('period', 'count'),
    total_kw=('capacity_selected', 'sum')
).sort_values(['period', 'tech_type', 'DNO'])

# Fill NaN capacity values with 0 for output (per Pipeline 1 contract handling)
dno_agg['total_kw'] = dno_agg['total_kw'].fillna(0)

print(f"DNO aggregation: {len(dno_agg):,} rows")
print(f"DNO install_count total: {dno_agg['install_count'].sum():,}")

# ============================================================================
# LSOA-level aggregation
# ============================================================================
print("Aggregating to LSOA level...")

# Load geography enrichment
lsoa_lookup = pd.read_csv(
    os.path.join(PROJECT_ROOT, "lookups", "LSOA to DNO.csv"),
    encoding='utf-8-sig'
)
lsoa_lookup = lsoa_lookup[['LSOA21CD', 'LAD22CD', 'MSOA21CD', 'MSOA21NM', 'Majority Licence area']].drop_duplicates()
lsoa_lookup.set_index('LSOA21CD', inplace=True)

# Only include rows with valid LSOA
lsoa_eligible = monthly_eligible[monthly_eligible["lsoa21cd"].notna()].copy()
lsoa_eligible['LSOA21CD'] = lsoa_eligible['lsoa21cd']

# Enrich lsoa_eligible with geography before aggregation
lsoa_eligible = lsoa_eligible.merge(
    lsoa_lookup,
    left_on='LSOA21CD',
    right_index=True,
    how='left',
    suffixes=('', '_lookup')
)

lsoa_agg = lsoa_eligible.groupby(['period', 'tech_type', 'LSOA21CD'], as_index=False).agg(
    install_count=('period', 'count'),
    total_kw=('capacity_selected', 'sum'),
    DNO=('DNO', 'first'),
    LAD22CD=('LAD22CD', 'first'),
    MSOA21CD=('MSOA21CD', 'first'),
    MSOA21NM=('MSOA21NM', 'first')
)

# Fill total_kw NaN with 0
lsoa_agg['total_kw'] = lsoa_agg['total_kw'].fillna(0)

# Select final columns in correct order
lsoa_final = lsoa_agg[[
    'period', 'tech_type', 'LSOA21CD', 'LAD22CD', 'MSOA21CD', 'MSOA21NM', 'DNO',
    'install_count', 'total_kw'
]].sort_values(['period', 'tech_type', 'LSOA21CD'])

print(f"LSOA aggregation: {len(lsoa_final):,} rows")
print(f"LSOA install_count total: {lsoa_final['install_count'].sum():,}")

# ============================================================================
# EV Vehicles: Load canonical source and enrich with geography
# ============================================================================
print("Loading EV vehicle actuals (canonical source)...")

ev_lsoa = pd.read_csv(os.path.join(PROJECT_ROOT, "project", "output_processed", "ev_quarterly_lsoa.csv"))

# Filter to same date range as pipeline (Apr 2025 - Mar 2026)
ev_lsoa = ev_lsoa[
    (ev_lsoa['period'] >= '2025-04') &
    (ev_lsoa['period'] <= '2026-03')
].copy()

# Load LAEP lookup for geography enrichment
laep_lookup = pd.read_csv(
    os.path.join(PROJECT_ROOT, "lookups", "LA-LAEP lookup.csv"),
    encoding='utf-8-sig'
)
laep_lookup = laep_lookup[['ID_CODE', 'LAEP']].drop_duplicates()
laep_lookup.columns = ['LAD22CD', 'LAEP']

# Merge EV data with LAEP via LAD22CD
# EV source already has LAD22CD embedded, but we need to load full lookup for consistency
lsoa_lookup_for_ev = pd.read_csv(
    os.path.join(PROJECT_ROOT, "lookups", "LSOA to DNO.csv"),
    encoding='utf-8-sig'
)
lsoa_lookup_for_ev = lsoa_lookup_for_ev[['LSOA21CD', 'LAD22CD', 'MSOA21CD', 'MSOA21NM', 'Majority Licence area']].drop_duplicates()

# Merge EV with full LSOA lookup to ensure consistent geography
ev_enriched = pd.merge(
    ev_lsoa[['period', 'LSOA21CD', 'DNO', 'install_count', 'total_kw', 'EV_Type']],
    lsoa_lookup_for_ev,
    left_on='LSOA21CD',
    right_on='LSOA21CD',
    how='left'
)

# Merge with LAEP lookup
ev_enriched = pd.merge(
    ev_enriched,
    laep_lookup,
    left_on='LAD22CD',
    right_on='LAD22CD',
    how='left'
)

# Rename tech_type column for EV (already set in source)
ev_enriched['tech_type'] = 'EV'

# ============================================================================
# Build complete canonical EV geography panel (11,023 LSOAs × 2 EV types per period)
# ============================================================================
print("\nBuilding canonical EV geography panel...")

# Build canonical LSOA base ONCE with all geography mappings
canonical_base = lsoa_lookup_for_ev[['LSOA21CD', 'LAD22CD', 'MSOA21CD', 'MSOA21NM', 'Majority Licence area']].drop_duplicates().copy()
canonical_base.columns = ['LSOA21CD', 'LAD22CD', 'MSOA21CD', 'MSOA21NM', 'DNO']

# Load LAEP lookup ONCE
laep_lookup = pd.read_csv(os.path.join(PROJECT_ROOT, "lookups", "LA-LAEP lookup.csv"), encoding='utf-8-sig')
laep_lookup = laep_lookup[['ID_CODE', 'LAEP']].drop_duplicates().copy()
laep_lookup.columns = ['LAD22CD', 'LAEP']

# Join LAEP to canonical base
canonical_base = canonical_base.merge(laep_lookup, on='LAD22CD', how='left')

# For each EV observation period, create complete panel
ev_panels = []

for period in sorted(ev_enriched['period'].unique()):
    period_data = ev_enriched[ev_enriched['period'] == period][['LSOA21CD', 'EV_Type', 'install_count']].copy()

    # Create complete canonical LSOA × EV_Type Cartesian product
    ev_types = ['BEV', 'PHEV']

    # Cross join EV types with canonical base
    panel_base = canonical_base.copy()
    panel_base['key'] = 1
    ev_type_df = pd.DataFrame({'EV_Type': ev_types, 'key': 1})
    complete_panel = panel_base.merge(ev_type_df, on='key', how='outer').drop('key', axis=1)

    # Add period and tech_type
    complete_panel['period'] = period
    complete_panel['tech_type'] = 'EV'

    # Left join observed stock
    panel_with_stock = complete_panel.merge(
        period_data,
        on=['LSOA21CD', 'EV_Type'],
        how='left'
    )

    # Fill missing stock with zeros (no observation for that LSOA × EV_Type)
    panel_with_stock['install_count'] = panel_with_stock['install_count'].fillna(0).astype(int)
    # EV total_kw = 0 is a schema compatibility sentinel only.
    # EV 'install_count' represents vehicle STOCK (not capacity).
    # This 0 must not be interpreted as measured zero electrical capacity.
    panel_with_stock['total_kw'] = 0.0

    # Remove rows with NaN LSOA21CD (canonical panel must be complete)
    panel_with_stock = panel_with_stock[panel_with_stock['LSOA21CD'].notna()].copy()

    # Select final columns in order
    panel_final = panel_with_stock[[
        'period', 'tech_type', 'LSOA21CD', 'LAD22CD', 'MSOA21CD', 'MSOA21NM', 'DNO',
        'install_count', 'total_kw', 'LAEP', 'EV_Type'
    ]].copy()

    # Remove any duplicates
    panel_final = panel_final.drop_duplicates(subset=['period', 'LSOA21CD', 'EV_Type'])

    ev_panels.append(panel_final)

    unique_lsoas = len(panel_final[['LSOA21CD']].drop_duplicates())
    print(f"  {period}: {len(panel_final):,} rows ({unique_lsoas:,} unique LSOAs)")

# Combine all periods
ev_lsoa_final = pd.concat(ev_panels, ignore_index=True)
ev_lsoa_final = ev_lsoa_final.sort_values(['period', 'tech_type', 'LSOA21CD', 'EV_Type']).reset_index(drop=True)

print(f"\nEV LSOA aggregation (complete canonical panel): {len(ev_lsoa_final):,} rows")
print(f"EV install_count total: {int(ev_lsoa_final['install_count'].sum()):,}")

# Build DNO-level output for EV
# For Stage 2F consistency, keep EV_Type split at DNO level
ev_dno_agg = ev_enriched.groupby(['period', 'tech_type', 'DNO', 'EV_Type'], as_index=False).agg(
    install_count=('install_count', 'sum'),
    total_kw=('total_kw', 'sum')
).sort_values(['period', 'tech_type', 'DNO', 'EV_Type'])

print(f"EV DNO aggregation: {len(ev_dno_agg):,} rows")
print(f"EV DNO install_count total: {ev_dno_agg['install_count'].sum():,}")

# Append EV data to existing outputs
# For LSOA: extend columns to include LAEP and EV_Type (new columns for EV only)
# For consistency, add LAEP and EV_Type columns to existing techs as nullable
lsoa_final['LAEP'] = None
lsoa_final['EV_Type'] = None

# Reorder EV columns to match extended schema
ev_lsoa_final_reordered = ev_lsoa_final[lsoa_final.columns]

# Combine LSOA data
lsoa_final = pd.concat([lsoa_final, ev_lsoa_final_reordered], ignore_index=True)
lsoa_final = lsoa_final.sort_values(['period', 'tech_type', 'LSOA21CD']).reset_index(drop=True)

# For DNO: add EV_Type to existing aggregations
dno_agg['EV_Type'] = None

# Combine DNO data
dno_agg = pd.concat([dno_agg, ev_dno_agg], ignore_index=True)
dno_agg = dno_agg.sort_values(['period', 'tech_type', 'DNO']).reset_index(drop=True)

print(f"\nCombined LSOA: {len(lsoa_final):,} rows (Heat Pump + Solar PV + EV)")
print(f"Combined DNO: {len(dno_agg):,} rows (Heat Pump + Solar PV + EV)")

# ============================================================================
# Write outputs
# ============================================================================
os.makedirs(OUTPUT_PROCESSED, exist_ok=True)

dno_path = os.path.join(OUTPUT_PROCESSED, "dashboard_data_dno.csv")
dno_agg.to_csv(dno_path, index=False)
print(f"\nDNO output: {dno_path}")

lsoa_path = os.path.join(OUTPUT_PROCESSED, "dashboard_data_lsoa.csv")
lsoa_final.to_csv(lsoa_path, index=False)
print(f"LSOA output: {lsoa_path}")

# Validation report
print(f"\n" + "=" * 80)
print(f"STAGE 2E VALIDATION REPORT")
print(f"=" * 80)

print(f"\nPopulation reconciliation:")
print(f"  Monthly-eligible (all dates): 624,102")
print(f"  Monthly-eligible (Apr 2025-Mar 2026): {len(monthly_eligible):,}")
print(f"  DNO install_count sum: {dno_agg['install_count'].sum():,}")
print(f"  LSOA install_count sum: {lsoa_final['install_count'].sum():,}")
print(f"  Unresolved LSOA in date range: {len(monthly_eligible) - lsoa_final['install_count'].sum():,}")

print(f"\nTechnology in outputs:")
print(f"  Unique in DNO: {sorted(dno_agg['tech_type'].unique())}")
print(f"  Unique in LSOA: {sorted(lsoa_final['tech_type'].unique())}")

print(f"\nDNO values:")
print(f"  {sorted(dno_agg['DNO'].unique())}")

# EV Validation
print(f"\n" + "=" * 80)
print(f"EV VEHICLE VALIDATION")
print(f"=" * 80)

ev_lsoa_data = lsoa_final[lsoa_final['tech_type'] == 'EV'].copy()
ev_dno_data = dno_agg[dno_agg['tech_type'] == 'EV'].copy()

if len(ev_lsoa_data) > 0:
    # Q1 2026 validation
    q1_ev = ev_lsoa_data[ev_lsoa_data['period'] == '2026-03'].copy()
    q1_bev = q1_ev[q1_ev['EV_Type'] == 'BEV']['install_count'].sum()
    q1_phev = q1_ev[q1_ev['EV_Type'] == 'PHEV']['install_count'].sum()
    q1_total = q1_bev + q1_phev

    print(f"\nQ1 2026 (31-Mar-2026) EV Stock (London-Adjusted Final):")
    print(f"  BEV: {q1_bev:,} (expected 598,878)")
    print(f"  PHEV: {q1_phev:,} (expected 310,086)")
    print(f"  TOTAL: {q1_total:,} (expected 908,964)")

    # DNO breakdown
    q1_dno = ev_dno_data[ev_dno_data['period'] == '2026-03'].copy()
    print(f"\nQ1 2026 DNO Breakdown:")
    for dno in ['EPN', 'LPN', 'SPN']:
        dno_total = q1_dno[q1_dno['DNO'] == dno]['install_count'].sum()
        print(f"  {dno}: {dno_total:,}")

    # BEV/PHEV split
    print(f"\nQ1 2026 BEV/PHEV Split:")
    q1_bev_sum = ev_lsoa_data[(ev_lsoa_data['period'] == '2026-03') & (ev_lsoa_data['EV_Type'] == 'BEV')]['install_count'].sum()
    q1_phev_sum = ev_lsoa_data[(ev_lsoa_data['period'] == '2026-03') & (ev_lsoa_data['EV_Type'] == 'PHEV')]['install_count'].sum()
    print(f"  BEV: {q1_bev_sum:,}")
    print(f"  PHEV: {q1_phev_sum:,}")
    print(f"  Total (BEV+PHEV): {q1_bev_sum + q1_phev_sum:,}")

print(f"\nStage 2E outputs generated successfully")
