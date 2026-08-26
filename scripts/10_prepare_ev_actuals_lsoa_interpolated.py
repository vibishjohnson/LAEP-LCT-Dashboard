#!/usr/bin/env python3
"""
Prepare EV Actuals at LSOA level with LINEAR INTERPOLATION between quarters
Produces monthly data for all 12 months of fiscal year Apr 2025 - Mar 2026
"""

import os
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "project", "output_processed")
LOOKUPS_DIR = os.path.join(PROJECT_ROOT, "lookups")

print("=" * 70)
print("EV Actuals - LSOA Level with Interpolation")
print("=" * 70)

# Load EV body type data
ev_path = os.path.join(OUTPUT_DIR, "lsoa_ev_bodytype.csv")
df_ev = pd.read_csv(ev_path)

print(f"\nLoaded EV data: {len(df_ev)} records")

# Load DNO mapping (for reference, though we keep LSOA granularity)
dno_lookup = pd.read_csv(
    os.path.join(LOOKUPS_DIR, "LSOA to DNO.csv"),
    encoding='utf-8-sig',
    usecols=['LSOA21CD', 'Majority Licence area']
)
dno_lookup.columns = ['LSOA21CD', 'DNO']
dno_lookup = dno_lookup.drop_duplicates()

# Merge DNO onto EV data
df_ev = df_ev.merge(dno_lookup, left_on='LSOA21CD', right_on='LSOA21CD', how='left')

# Convert period to timestamp
df_ev['period_date'] = pd.to_datetime(df_ev['period'])

# Aggregate by quarter, LSOA21CD, DNO, and Fuel (BEV/PHEV)
quarterly_data = df_ev.groupby(['period_date', 'LSOA21CD', 'DNO', 'Fuel'])['ev_count'].sum().reset_index()
quarterly_data.columns = ['period_date', 'LSOA21CD', 'DNO', 'EV_Type', 'install_count']

print(f"\nQuarterly aggregates: {len(quarterly_data)} records")

# Interpolate between quarters to create monthly values
monthly_records = []

fiscal_months = [
    ('2025-04', 4),   # Apr
    ('2025-05', 5),   # May
    ('2025-06', 6),   # Jun
    ('2025-07', 7),   # Jul
    ('2025-08', 8),   # Aug
    ('2025-09', 9),   # Sep
    ('2025-10', 10),  # Oct
    ('2025-11', 11),  # Nov
    ('2025-12', 12),  # Dec
    ('2026-01', 1),   # Jan
    ('2026-02', 2),   # Feb
    ('2026-03', 3),   # Mar
]

for (lsoa, dno), group in quarterly_data.groupby(['LSOA21CD', 'DNO']):
    for ev_type in group['EV_Type'].unique():
        subset = group[group['EV_Type'] == ev_type].sort_values('period_date')

        if len(subset) < 2:
            continue

        # Create lookup: date -> value
        values_by_date = dict(zip(subset['period_date'], subset['install_count']))

        # Interpolate for each fiscal month
        for month_str, month_num in fiscal_months:
            # Determine interpolation parameters
            if month_num in [4, 5]:  # Apr, May
                q_start = pd.Timestamp('2025-03-31')
                q_end = pd.Timestamp('2025-06-30')
                progress = (month_num - 3) / 3.0
            elif month_num in [7, 8]:  # Jul, Aug
                q_start = pd.Timestamp('2025-06-30')
                q_end = pd.Timestamp('2025-09-30')
                progress = (month_num - 6) / 3.0
            elif month_num in [10, 11]:  # Oct, Nov
                q_start = pd.Timestamp('2025-09-30')
                q_end = pd.Timestamp('2025-12-31')
                progress = (month_num - 9) / 3.0
            elif month_num in [1, 2, 3]:  # Jan, Feb, Mar (hold Q4)
                q_start = pd.Timestamp('2025-12-31')
                q_end = pd.Timestamp('2025-12-31')
                progress = 0.0
            elif month_num in [6, 9, 12, 3]:  # Exact quarter ends
                q_start = q_end = pd.Timestamp(f"{2025 if month_num < 12 else 2026}-{month_num:02d}-{[31,28,31,30,31,30,31,31,30,31,30,31][month_num-1]}")
                if month_num == 6:
                    q_start = q_end = pd.Timestamp('2025-06-30')
                elif month_num == 9:
                    q_start = q_end = pd.Timestamp('2025-09-30')
                elif month_num == 12:
                    q_start = q_end = pd.Timestamp('2025-12-31')
                elif month_num == 3:
                    q_start = q_end = pd.Timestamp('2025-03-31')
                progress = 0.0

            # Interpolate
            val_start = values_by_date.get(q_start, 0)
            val_end = values_by_date.get(q_end, 0)
            interpolated = val_start + (val_end - val_start) * progress

            monthly_records.append({
                'period': month_str,
                'tech_type': 'EV',
                'LSOA21CD': lsoa,
                'DNO': dno,
                'EV_Type': ev_type,
                'install_count': int(round(interpolated)),
                'total_kw': 0.0
            })

df_monthly = pd.DataFrame(monthly_records)

print(f"\nMonthly interpolated records: {len(df_monthly)}")
print(f"Unique LSOAs: {df_monthly['LSOA21CD'].nunique()}")
print(f"Unique DNOs: {df_monthly['DNO'].nunique()}")

# Save
out_path = os.path.join(OUTPUT_DIR, "ev_actuals_lsoa.csv")
df_monthly[['period', 'tech_type', 'LSOA21CD', 'DNO', 'EV_Type', 'install_count', 'total_kw']].to_csv(out_path, index=False)

print(f"\nEV Actuals (LSOA level, interpolated): {out_path}")
print(f"Total records: {len(df_monthly)}")

print("\n" + "=" * 70)
print("Done. LSOA-level monthly data ready for dashboard.")
