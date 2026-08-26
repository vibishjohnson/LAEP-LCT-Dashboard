#!/usr/bin/env python3
"""
Prepare EV Actuals from 01_ev_processing output (lsoa_ev_bodytype.csv)
Aggregates by DNO and Fuel type (BEV/PHEV) for dashboard
"""

import os
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "project", "output_processed")
LOOKUPS_DIR = os.path.join(PROJECT_ROOT, "lookups")

print("=" * 70)
print("EV Actuals from EV Processing (lsoa_ev_bodytype.csv)")
print("=" * 70)

# Load EV body type data
ev_path = os.path.join(OUTPUT_DIR, "lsoa_ev_bodytype.csv")
df_ev = pd.read_csv(ev_path)

print(f"\nLoaded EV data: {len(df_ev)} records")
print(f"Columns: {df_ev.columns.tolist()}")
print(f"Periods: {sorted(df_ev['period'].unique())}")
print(f"Fuel types: {sorted(df_ev['Fuel'].unique())}")
print(f"Body types: {sorted(df_ev['BodyType'].unique())}")

# Load DNO mapping
dno_lookup = pd.read_csv(
    os.path.join(LOOKUPS_DIR, "LSOA to DNO.csv"),
    encoding='utf-8-sig',
    usecols=['LSOA21CD', 'Majority Licence area']
)
dno_lookup.columns = ['LSOA21CD', 'DNO']
dno_lookup = dno_lookup.drop_duplicates()

# Merge DNO onto EV data
df_ev = df_ev.merge(dno_lookup, left_on='LSOA21CD', right_on='LSOA21CD', how='left')

# Convert period to month format (YYYY-MM)
# e.g., 2025-09-30 -> 2025-09
df_ev['period'] = pd.to_datetime(df_ev['period']).dt.to_period('M').astype(str)

print(f"\nPeriods after conversion: {sorted(df_ev['period'].unique())}")

# Aggregate by period, DNO, and Fuel (BEV/PHEV)
# Sum all body types together
agg_data = df_ev.groupby(['period', 'DNO', 'Fuel'])['ev_count'].sum().reset_index()
agg_data.columns = ['period', 'DNO', 'EV_Type', 'install_count']
agg_data['tech_type'] = 'EV'
agg_data['total_kw'] = 0.0

print(f"\nAggregated to DNO level: {len(agg_data)} records")
print(f"\nSample data:")
print(agg_data.head(12))

print(f"\n\nEV Stock Summary by Fuel Type:")
for fuel in sorted(agg_data['EV_Type'].unique()):
    fuel_data = agg_data[agg_data['EV_Type'] == fuel]
    total = fuel_data['install_count'].sum()
    avg = fuel_data.groupby('period')['install_count'].sum().mean()
    print(f"  {fuel}: Total {total:,} (Avg per month: {int(avg):,})")

# Save
out_path = os.path.join(OUTPUT_DIR, "ev_actuals_dno.csv")
agg_data[['period', 'tech_type', 'DNO', 'EV_Type', 'install_count', 'total_kw']].to_csv(out_path, index=False)

print(f"\nEV Actuals (DNO level): {out_path}")
print(f"Total records: {len(agg_data)}")

print("\n" + "=" * 70)
print("Done. Data ready for dashboard comparison.")
