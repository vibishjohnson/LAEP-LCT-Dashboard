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

print(f"\nStage 2E outputs generated successfully")
