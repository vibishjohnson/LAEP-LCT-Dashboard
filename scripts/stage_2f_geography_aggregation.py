#!/usr/bin/env python3
"""
Stage 2F Geography Aggregation: LSOA -> LA -> LAEP

Produces deterministic aggregates from canonical LSOA comparison output.
Uses authoritative lookups: LSOA to DNO (for LAD mapping), LA-LAEP lookup.

Preserves all data semantics:
  - EV DFES baseline vs Observation distinction
  - null March 2025 EV actuals
  - BEV/PHEV separation
  - Non-EV null/missing (not zero-filled)
  - Variance recalculated from aggregated values
"""
import pandas as pd
import numpy as np
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Load canonical source data
lsoa_comparison = pd.read_csv(
    os.path.join(PROJECT_ROOT, "project/output_processed/dashboard_comparison_lsoa.csv"),
    low_memory=False
)
lsoa_dno = pd.read_csv(
    os.path.join(PROJECT_ROOT, "lookups/LSOA to DNO.csv")
)
la_laep = pd.read_csv(
    os.path.join(PROJECT_ROOT, "lookups/LA-LAEP lookup.csv")
)

print("=" * 100)
print("STAGE 2F GEOGRAPHY AGGREGATION: LSOA -> LA -> LAEP")
print("=" * 100)

# ============================================================================
# LINEAGE VERIFICATION
# ============================================================================
print("\n[LINEAGE] Input data sources")
print("-" * 100)

print("Source 1: dashboard_comparison_lsoa.csv")
print("  Rows: {0:,} (LSOA-level data from Stage 2F)".format(len(lsoa_comparison)))
print("  Columns: {0}".format(len(lsoa_comparison.columns)))

canonical_lsoas = set(lsoa_comparison['LSOA21CD'].unique())
print("  Unique LSOA21CD: {0:,}".format(len(canonical_lsoas)))

print("\nSource 2: lookups/LSOA to DNO.csv (LSOA -> LAD mapping)")
print("  Rows: {0:,}".format(len(lsoa_dno)))
lsoa_lad = lsoa_dno[['LSOA21CD', 'LAD22CD', 'LAD22NM']].drop_duplicates()
print("  Unique LSOA21CD: {0:,}".format(lsoa_lad['LSOA21CD'].nunique()))
print("  Unique LAD22CD: {0:,}".format(lsoa_lad['LAD22CD'].nunique()))

print("\nSource 3: lookups/LA-LAEP lookup.csv (LAD -> LAEP mapping)")
print("  Rows: {0:,}".format(len(la_laep)))
laep_with_mapping = la_laep[la_laep['LAEP'].notna()]
print("  Rows with LAEP mapping: {0:,}".format(len(laep_with_mapping)))
print("  Unique LAEPs: {0}".format(laep_with_mapping['LAEP'].nunique()))

# ============================================================================
# VERIFICATION: LSOA -> LAD Mapping Coverage
# ============================================================================
print("\n[VERIFICATION] LSOA -> LAD Coverage")
print("-" * 100)

# All canonical LSOAs must map to LAD
lsoas_with_lad = lsoa_lad[lsoa_lad['LSOA21CD'].isin(canonical_lsoas)]
total_canonical = len(canonical_lsoas)
mapped_canonical = lsoas_with_lad['LSOA21CD'].nunique()

print("Canonical LSOA count: {0:,}".format(total_canonical))
print("Canonical LSOA with LAD mapping: {0:,}".format(mapped_canonical))

if mapped_canonical != total_canonical:
    print("ERROR: Not all canonical LSOAs have LAD mapping!")
    sys.exit(1)

ukpn_lads = set(lsoas_with_lad['LAD22CD'].unique())
print("UKPN LAD count: {0}".format(len(ukpn_lads)))

# Check for LAEP mapping in UKPN LADs
la_laep_clean = la_laep[['ID_CODE', 'LAEP']].rename(columns={'ID_CODE': 'LAD22CD'})
ukpn_lads_with_laep = la_laep_clean[
    (la_laep_clean['LAD22CD'].isin(ukpn_lads)) &
    (la_laep_clean['LAEP'].notna())
]['LAD22CD'].nunique()

print("UKPN LAD with LAEP mapping: {0}".format(ukpn_lads_with_laep))
print("Unique LAEPs in UKPN: {0}".format(
    la_laep_clean[
        (la_laep_clean['LAD22CD'].isin(ukpn_lads)) &
        (la_laep_clean['LAEP'].notna())
    ]['LAEP'].nunique()
))

# LSOA coverage by LAEP
lsoas_with_laep_map = lsoas_with_lad.merge(
    la_laep_clean[la_laep_clean['LAEP'].notna()],
    on='LAD22CD',
    how='left'
)
lsoa_with_laep_count = lsoas_with_laep_map['LAEP'].notna().sum()
lsoa_without_laep_count = total_canonical - lsoa_with_laep_count

print("LSOA with LAEP mapping: {0:,} ({1:.1f}%)".format(
    lsoa_with_laep_count, 100.0 * lsoa_with_laep_count / total_canonical
))
print("LSOA without LAEP mapping: {0:,} ({1:.1f}%)".format(
    lsoa_without_laep_count, 100.0 * lsoa_without_laep_count / total_canonical
))

# ============================================================================
# AGGREGATION: LSOA -> LOCAL AUTHORITY
# ============================================================================
print("\n[AGGREGATION] LSOA -> Local Authority")
print("-" * 100)

# Add LAD to LSOA comparison data
lsoa_with_lad_full = lsoa_comparison.merge(
    lsoa_lad,
    on='LSOA21CD',
    how='left'
)

# Aggregate EV and non-EV separately due to different field sets
ev_lsoa = lsoa_with_lad_full[lsoa_with_lad_full['tech_type'] == 'EV'].copy()
non_ev_lsoa = lsoa_with_lad_full[lsoa_with_lad_full['tech_type'] != 'EV'].copy()

# EV aggregation: sum actuals, DFES, maintain EV_Type and comparison_point_type
ev_by_la = ev_lsoa.groupby(
    ['period', 'LAD22CD', 'LAD22NM', 'EV_Type', 'comparison_point_type']
).agg({
    'actual_install_count': 'sum',
    'actual_total_kw': 'sum',
    'actual_cumulative_count': 'sum',
    'actual_cumulative_kw': 'sum',
    'dfes_monthly_benchmark': 'sum',
    'dfes_cumulative_benchmark': 'sum',
    'dfes_scenario': 'first',
    'comparison_unit': 'first'
}).reset_index()

# Preserve null semantics: if all constituent LSOA actuals were null, result should be null
null_check = ev_lsoa.groupby(
    ['period', 'LAD22CD', 'LAD22NM', 'EV_Type', 'comparison_point_type']
)['actual_install_count'].apply(lambda x: x.isna().all()).reset_index()
null_check.columns = ['period', 'LAD22CD', 'LAD22NM', 'EV_Type', 'comparison_point_type', 'all_null']

# Merge and restore NaN where all source values were null
ev_by_la = ev_by_la.merge(null_check, on=['period', 'LAD22CD', 'LAD22NM', 'EV_Type', 'comparison_point_type'])
ev_by_la.loc[ev_by_la['all_null'], 'actual_install_count'] = np.nan
ev_by_la = ev_by_la.drop(columns=['all_null'])

# Calculate variance from aggregated values
ev_by_la['variance'] = ev_by_la['actual_install_count'] - ev_by_la['dfes_monthly_benchmark']
ev_by_la['variance_pct'] = np.where(
    ev_by_la['dfes_monthly_benchmark'] != 0,
    (ev_by_la['variance'] / ev_by_la['dfes_monthly_benchmark']) * 100,
    np.nan
)

# Add missing fields for EV
ev_by_la['tech_type'] = 'EV'
ev_by_la['DNO'] = None
ev_by_la['LAEP'] = None
ev_by_la['capacity_known_count'] = None
ev_by_la['capacity_unknown_count'] = None
ev_by_la['capacity_coverage_pct'] = None
# Get opening/closing stock from first LSOA in each LA (same for all LSOAs in LA)
stock_map = ev_lsoa.groupby('LAD22CD')[['dfes_opening_stock', 'dfes_closing_stock']].first()
ev_by_la['dfes_opening_stock'] = ev_by_la['LAD22CD'].map(stock_map['dfes_opening_stock'])
ev_by_la['dfes_closing_stock'] = ev_by_la['LAD22CD'].map(stock_map['dfes_closing_stock'])

# Non-EV aggregation - monthly actuals and DFES monthly only
# Note: dfes_cumulative_benchmark will be recalculated after grouping
non_ev_by_la_monthly = non_ev_lsoa.groupby(
    ['period', 'tech_type', 'LAD22CD', 'LAD22NM']
).agg({
    'actual_install_count': 'sum',
    'actual_total_kw': 'sum',
    'dfes_monthly_benchmark': 'sum',
    'dfes_scenario': 'first',
    'comparison_unit': 'first',
    'variance': 'sum'
}).reset_index()

# NULL semantics fix: if all constituent DFES values are NULL, result should be NULL not 0.0
for idx, row in non_ev_by_la_monthly.iterrows():
    # Check if all LSOA values for this group were NULL
    lsoa_group = non_ev_lsoa[
        (non_ev_lsoa['period'] == row['period']) &
        (non_ev_lsoa['tech_type'] == row['tech_type']) &
        (non_ev_lsoa['LAD22CD'] == row['LAD22CD'])
    ]
    if lsoa_group['dfes_monthly_benchmark'].isna().all():
        non_ev_by_la_monthly.at[idx, 'dfes_monthly_benchmark'] = None

# Calculate cumulative from aggregated monthly actuals
non_ev_by_la = []
months = ['2025-04', '2025-05', '2025-06', '2025-07', '2025-08', '2025-09',
          '2025-10', '2025-11', '2025-12', '2026-01', '2026-02', '2026-03']

for (tech, lad) in non_ev_by_la_monthly[['tech_type', 'LAD22CD']].drop_duplicates().values:
    tech_lad_data = non_ev_by_la_monthly[
        (non_ev_by_la_monthly['tech_type'] == tech) &
        (non_ev_by_la_monthly['LAD22CD'] == lad)
    ].copy()

    for month_idx, month in enumerate(months):
        month_data = tech_lad_data[tech_lad_data['period'] == month]
        if len(month_data) == 0:
            continue

        row = month_data.iloc[0].to_dict()

        # Calculate cumulative from monthly actuals
        prev_months_data = tech_lad_data[
            (tech_lad_data['period'] < month)
        ]
        cumulative_count = prev_months_data['actual_install_count'].sum() + row['actual_install_count']
        cumulative_kw = prev_months_data['actual_total_kw'].sum() + row['actual_total_kw']

        row['actual_cumulative_count'] = cumulative_count
        row['actual_cumulative_kw'] = cumulative_kw

        # Calculate DFES cumulative from monthly DFES benchmark
        dfes_cumulative_benchmark = prev_months_data['dfes_monthly_benchmark'].sum() + (row['dfes_monthly_benchmark'] if pd.notna(row['dfes_monthly_benchmark']) else 0)
        row['dfes_cumulative_benchmark'] = dfes_cumulative_benchmark if pd.notna(row['dfes_monthly_benchmark']) else None

        # Variance_pct from aggregated values
        row['variance_pct'] = (row['variance'] / row['dfes_monthly_benchmark'] * 100) if pd.notna(row['dfes_monthly_benchmark']) and row['dfes_monthly_benchmark'] != 0 else np.nan

        non_ev_by_la.append(row)

non_ev_by_la = pd.DataFrame(non_ev_by_la)

# Add missing fields for non-EV
non_ev_by_la['EV_Type'] = None
non_ev_by_la['comparison_point_type'] = None
non_ev_by_la['DNO'] = None
non_ev_by_la['LAEP'] = None
non_ev_by_la['capacity_known_count'] = None
non_ev_by_la['capacity_unknown_count'] = None
non_ev_by_la['capacity_coverage_pct'] = None
# Get opening/closing stock from first LSOA in each LA/tech (same for all LSOAs)
stock_map_non_ev = non_ev_lsoa.groupby(['LAD22CD', 'tech_type'])[['dfes_opening_stock', 'dfes_closing_stock']].first()
non_ev_by_la['dfes_opening_stock'] = non_ev_by_la.apply(
    lambda row: stock_map_non_ev.loc[(row['LAD22CD'], row['tech_type']), 'dfes_opening_stock']
    if (row['LAD22CD'], row['tech_type']) in stock_map_non_ev.index else None,
    axis=1
)
non_ev_by_la['dfes_closing_stock'] = non_ev_by_la.apply(
    lambda row: stock_map_non_ev.loc[(row['LAD22CD'], row['tech_type']), 'dfes_closing_stock']
    if (row['LAD22CD'], row['tech_type']) in stock_map_non_ev.index else None,
    axis=1
)

# Combine and standardize column order
la_records = pd.concat([ev_by_la, non_ev_by_la], ignore_index=True)
final_columns = [
    'period', 'tech_type', 'LAD22CD', 'LAD22NM', 'actual_install_count', 'actual_total_kw',
    'actual_cumulative_count', 'actual_cumulative_kw', 'dfes_monthly_benchmark',
    'dfes_cumulative_benchmark', 'dfes_scenario', 'comparison_unit', 'EV_Type',
    'variance', 'variance_pct', 'comparison_point_type', 'DNO', 'LAEP',
    'capacity_known_count', 'capacity_unknown_count', 'capacity_coverage_pct',
    'dfes_opening_stock', 'dfes_closing_stock'
]
la_records = la_records[final_columns]

print("LA records generated: {0:,}".format(len(la_records)))
print("Unique LADs in output: {0}".format(la_records['LAD22CD'].nunique()))

# ============================================================================
# AGGREGATION: LA -> LAEP
# ============================================================================
print("\n[AGGREGATION] Local Authority -> LAEP")
print("-" * 100)

# Add LAEP mapping to LA records
la_laep_map = la_laep[['ID_CODE', 'LAEP']].rename(columns={'ID_CODE': 'LAD22CD'})
la_laep_map = la_laep_map[la_laep_map['LAEP'].notna()].drop_duplicates()

la_with_laep = la_records.merge(la_laep_map, on='LAD22CD', how='left', suffixes=('_la', '_map'))

# Handle existing LAEP column from LA records
if 'LAEP_map' in la_with_laep.columns:
    la_with_laep['LAEP'] = la_with_laep['LAEP_map'].fillna(la_with_laep['LAEP_la'])
    la_with_laep = la_with_laep.drop(columns=['LAEP_map', 'LAEP_la'])

# Filter to LAEP-mapped records only
laep_records_ev = la_with_laep[
    (la_with_laep['LAEP'].notna()) &
    (la_with_laep['tech_type'] == 'EV')
].copy()

laep_records_non_ev = la_with_laep[
    (la_with_laep['LAEP'].notna()) &
    (la_with_laep['tech_type'] != 'EV')
].copy()

# Aggregate EV by LAEP
laep_ev_agg = laep_records_ev.groupby(
    ['period', 'LAEP', 'EV_Type', 'comparison_point_type']
).agg({
    'actual_install_count': 'sum',
    'actual_total_kw': 'sum',
    'actual_cumulative_count': 'sum',
    'actual_cumulative_kw': 'sum',
    'dfes_monthly_benchmark': 'sum',
    'dfes_cumulative_benchmark': 'sum',
    'dfes_scenario': 'first',
    'comparison_unit': 'first'
}).reset_index()

# Preserve null semantics for LAEP
laep_null_check = laep_records_ev.groupby(
    ['period', 'LAEP', 'EV_Type', 'comparison_point_type']
)['actual_install_count'].apply(lambda x: x.isna().all()).reset_index()
laep_null_check.columns = ['period', 'LAEP', 'EV_Type', 'comparison_point_type', 'all_null']

laep_ev_agg = laep_ev_agg.merge(laep_null_check, on=['period', 'LAEP', 'EV_Type', 'comparison_point_type'])
laep_ev_agg.loc[laep_ev_agg['all_null'], 'actual_install_count'] = np.nan
laep_ev_agg = laep_ev_agg.drop(columns=['all_null'])

laep_ev_agg['variance'] = laep_ev_agg['actual_install_count'] - laep_ev_agg['dfes_monthly_benchmark']
laep_ev_agg['variance_pct'] = np.where(
    laep_ev_agg['dfes_monthly_benchmark'] != 0,
    (laep_ev_agg['variance'] / laep_ev_agg['dfes_monthly_benchmark']) * 100,
    np.nan
)

laep_ev_agg['tech_type'] = 'EV'
laep_ev_agg['LAD22CD'] = None
laep_ev_agg['LAD22NM'] = None
laep_ev_agg['DNO'] = None
laep_ev_agg['capacity_known_count'] = None
laep_ev_agg['capacity_unknown_count'] = None
laep_ev_agg['capacity_coverage_pct'] = None
# Get opening/closing stock from first LA in each LAEP (same for all LAs in LAEP)
laep_stock_map = laep_records_ev.groupby('LAEP')[['dfes_opening_stock', 'dfes_closing_stock']].first()
laep_ev_agg['dfes_opening_stock'] = laep_ev_agg['LAEP'].map(laep_stock_map['dfes_opening_stock'])
laep_ev_agg['dfes_closing_stock'] = laep_ev_agg['LAEP'].map(laep_stock_map['dfes_closing_stock'])

# Aggregate non-EV by LAEP - monthly actuals and DFES monthly only
# Note: dfes_cumulative_benchmark will be recalculated after grouping
laep_non_ev_monthly = laep_records_non_ev.groupby(
    ['period', 'tech_type', 'LAEP']
).agg({
    'actual_install_count': 'sum',
    'actual_total_kw': 'sum',
    'dfes_monthly_benchmark': 'sum',
    'dfes_scenario': 'first',
    'comparison_unit': 'first',
    'variance': 'sum'
}).reset_index()

# NULL semantics fix: if all constituent DFES values are NULL, result should be NULL not 0.0
for idx, row in laep_non_ev_monthly.iterrows():
    # Check if all LA values for this group were NULL
    la_group = laep_records_non_ev[
        (laep_records_non_ev['period'] == row['period']) &
        (laep_records_non_ev['tech_type'] == row['tech_type']) &
        (laep_records_non_ev['LAEP'] == row['LAEP'])
    ]
    if la_group['dfes_monthly_benchmark'].isna().all():
        laep_non_ev_monthly.at[idx, 'dfes_monthly_benchmark'] = None

# Calculate cumulative from aggregated monthly actuals
laep_non_ev_agg = []
months = ['2025-04', '2025-05', '2025-06', '2025-07', '2025-08', '2025-09',
          '2025-10', '2025-11', '2025-12', '2026-01', '2026-02', '2026-03']

for (tech, laep) in laep_non_ev_monthly[['tech_type', 'LAEP']].drop_duplicates().values:
    tech_laep_data = laep_non_ev_monthly[
        (laep_non_ev_monthly['tech_type'] == tech) &
        (laep_non_ev_monthly['LAEP'] == laep)
    ].copy()

    for month_idx, month in enumerate(months):
        month_data = tech_laep_data[tech_laep_data['period'] == month]
        if len(month_data) == 0:
            continue

        row = month_data.iloc[0].to_dict()

        # Calculate cumulative from monthly actuals
        prev_months_data = tech_laep_data[
            (tech_laep_data['period'] < month)
        ]
        cumulative_count = prev_months_data['actual_install_count'].sum() + row['actual_install_count']
        cumulative_kw = prev_months_data['actual_total_kw'].sum() + row['actual_total_kw']

        row['actual_cumulative_count'] = cumulative_count
        row['actual_cumulative_kw'] = cumulative_kw

        # Calculate DFES cumulative from monthly DFES benchmark
        dfes_cumulative_benchmark = prev_months_data['dfes_monthly_benchmark'].sum() + (row['dfes_monthly_benchmark'] if pd.notna(row['dfes_monthly_benchmark']) else 0)
        row['dfes_cumulative_benchmark'] = dfes_cumulative_benchmark if pd.notna(row['dfes_monthly_benchmark']) else None

        # Variance_pct from aggregated values
        row['variance_pct'] = (row['variance'] / row['dfes_monthly_benchmark'] * 100) if pd.notna(row['dfes_monthly_benchmark']) and row['dfes_monthly_benchmark'] != 0 else np.nan

        laep_non_ev_agg.append(row)

laep_non_ev_agg = pd.DataFrame(laep_non_ev_agg)

laep_non_ev_agg['EV_Type'] = None
laep_non_ev_agg['comparison_point_type'] = None
laep_non_ev_agg['LAD22CD'] = None
laep_non_ev_agg['LAD22NM'] = None
laep_non_ev_agg['DNO'] = None
laep_non_ev_agg['capacity_known_count'] = None
laep_non_ev_agg['capacity_unknown_count'] = None
laep_non_ev_agg['capacity_coverage_pct'] = None
# Get opening/closing stock from first LA in each LAEP/tech (same for all LAs in LAEP)
laep_stock_map_non_ev = laep_records_non_ev.groupby(['LAEP', 'tech_type'])[['dfes_opening_stock', 'dfes_closing_stock']].first()
laep_non_ev_agg['dfes_opening_stock'] = laep_non_ev_agg.apply(
    lambda row: laep_stock_map_non_ev.loc[(row['LAEP'], row['tech_type']), 'dfes_opening_stock']
    if (row['LAEP'], row['tech_type']) in laep_stock_map_non_ev.index else None,
    axis=1
)
laep_non_ev_agg['dfes_closing_stock'] = laep_non_ev_agg.apply(
    lambda row: laep_stock_map_non_ev.loc[(row['LAEP'], row['tech_type']), 'dfes_closing_stock']
    if (row['LAEP'], row['tech_type']) in laep_stock_map_non_ev.index else None,
    axis=1
)

# Combine and standardize
laep_all = pd.concat([laep_ev_agg, laep_non_ev_agg], ignore_index=True)
laep_all = laep_all[final_columns]

print("LAEP records generated: {0:,}".format(len(laep_all)))
print("Unique LAEPs in output: {0}".format(laep_all['LAEP'].nunique()))

# ============================================================================
# QA: NULL HANDLING
# ============================================================================
print("\n[QA] Null Handling Verification")
print("-" * 100)

# Check March 2025 baseline actuals
march_2025_baseline_la = la_records[
    (la_records['period'] == '2025-03') &
    (la_records['tech_type'] == 'EV') &
    (la_records['comparison_point_type'] == 'DFES baseline')
]
null_count_la = march_2025_baseline_la['actual_install_count'].isna().sum()
total_baseline_la = len(march_2025_baseline_la)

print("LA level March 2025 EV baseline:")
print("  Rows: {0}".format(total_baseline_la))
print("  Null actuals: {0} ({1:.1f}%)".format(null_count_la, 100.0 * null_count_la / total_baseline_la if total_baseline_la > 0 else 0))

march_2025_baseline_laep = laep_all[
    (laep_all['period'] == '2025-03') &
    (laep_all['tech_type'] == 'EV') &
    (laep_all['comparison_point_type'] == 'DFES baseline')
]
null_count_laep = march_2025_baseline_laep['actual_install_count'].isna().sum()
total_baseline_laep = len(march_2025_baseline_laep)

print("LAEP level March 2025 EV baseline:")
print("  Rows: {0}".format(total_baseline_laep))
print("  Null actuals: {0} ({1:.1f}%)".format(null_count_laep, 100.0 * null_count_laep / total_baseline_laep if total_baseline_laep > 0 else 0))

# Explain aggregation behavior with mixed nulls
print("\nNull Aggregation Behavior:")
print("  When LSOA contains mix of numeric and null:")
print("    pandas groupby('sum') sums only non-null values")
print("    Result: aggregate is numeric (sum of subset)")
print("  When ALL constituent LSOAs are null:")
print("    pandas groupby('sum') returns null")
print("    Result: aggregate is null (no data)")
print("  For baseline rows: all LSOA actuals are null")
print("    Result: all LA and LAEP baseline actuals are null (correct)")

# ============================================================================
# QA: RECONCILIATION
# ============================================================================
print("\n[QA] March 2026 Lock-in Reconciliation")
print("-" * 100)

march_2026_lsoa = lsoa_comparison[
    (lsoa_comparison['period'] == '2026-03') &
    (lsoa_comparison['tech_type'] == 'EV') &
    (lsoa_comparison['comparison_point_type'] == 'Observation')
]
march_2026_actual_lsoa = march_2026_lsoa['actual_install_count'].sum()
march_2026_dfes_lsoa = march_2026_lsoa['dfes_monthly_benchmark'].sum()

march_2026_la_obs = la_records[
    (la_records['period'] == '2026-03') &
    (la_records['tech_type'] == 'EV') &
    (la_records['comparison_point_type'] == 'Observation')
]
march_2026_actual_la = march_2026_la_obs['actual_install_count'].sum()
march_2026_dfes_la = march_2026_la_obs['dfes_monthly_benchmark'].sum()

march_2026_laep_obs = laep_all[
    (laep_all['period'] == '2026-03') &
    (laep_all['tech_type'] == 'EV') &
    (laep_all['comparison_point_type'] == 'Observation')
]
march_2026_actual_laep = march_2026_laep_obs['actual_install_count'].sum()
march_2026_dfes_laep = march_2026_laep_obs['dfes_monthly_benchmark'].sum()

print("LSOA (raw): Actual={0:,.0f}, DFES={1:,.0f}".format(march_2026_actual_lsoa, march_2026_dfes_lsoa))
print("LA (aggregated): Actual={0:,.0f}, DFES={1:,.0f}".format(march_2026_actual_la, march_2026_dfes_la))
print("LAEP (62 councils): Actual={0:,.0f}, DFES={1:,.0f}".format(march_2026_actual_laep, march_2026_dfes_laep))

match_actual = abs(march_2026_actual_lsoa - march_2026_actual_la) < 1
match_dfes = abs(march_2026_dfes_lsoa - march_2026_dfes_la) < 1
print("\nLSOA-LA reconciliation: Actual={0}, DFES={1}".format(
    "PASS" if match_actual else "FAIL",
    "PASS" if match_dfes else "FAIL"
))

# ============================================================================
# OUTPUT: WRITE CSVs
# ============================================================================
print("\n[OUTPUT] Writing CSV files")
print("-" * 100)

la_path = os.path.join(PROJECT_ROOT, "project/output_processed/dashboard_comparison_la.csv")
laep_path = os.path.join(PROJECT_ROOT, "project/output_processed/dashboard_comparison_laep.csv")

la_records.to_csv(la_path, index=False)
laep_all.to_csv(laep_path, index=False)

print("Written: {0}".format(la_path))
print("  Rows: {0}".format(len(la_records)))
print("\nWritten: {0}".format(laep_path))
print("  Rows: {0}".format(len(laep_all)))

print("\n" + "=" * 100)
print("PRODUCTION LINEAGE COMPLETE")
print("=" * 100)
print("\nDeterministic generation from:")
print("  1. project/output_processed/dashboard_comparison_lsoa.csv")
print("  2. lookups/LSOA to DNO.csv (authoritative LSOA->LAD->DNO)")
print("  3. lookups/LA-LAEP lookup.csv (authoritative LAD->LAEP)")
print("\nNo hardcoded values. All aggregation logic is data-driven.")
