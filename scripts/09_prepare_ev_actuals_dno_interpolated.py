#!/usr/bin/env python3
"""
Prepare EV Actuals from EV Processing with LINEAR INTERPOLATION between quarters
Produces monthly data for all 12 months of fiscal year Apr 2025 - Mar 2026
"""

import os
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "project", "output_processed")
LOOKUPS_DIR = os.path.join(PROJECT_ROOT, "lookups")

print("=" * 70)
print("EV Actuals - Interpolated Monthly (LSOA -> DNO)")
print("=" * 70)

# Load EV body type data
ev_path = os.path.join(OUTPUT_DIR, "lsoa_ev_bodytype.csv")
df_ev = pd.read_csv(ev_path)

print(f"\nLoaded EV data: {len(df_ev)} records")
print(f"Periods: {sorted(df_ev['period'].unique())}")

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

# Convert period to quarter-end format (YYYY-MM-DD)
df_ev['period_date'] = pd.to_datetime(df_ev['period'])

# Aggregate by quarter, DNO, and Fuel (BEV/PHEV)
quarterly_data = df_ev.groupby(['period_date', 'DNO', 'Fuel'])['ev_count'].sum().reset_index()
quarterly_data.columns = ['period_date', 'DNO', 'EV_Type', 'install_count']

print(f"\nQuarterly aggregates: {len(quarterly_data)} records")
print(f"Sample quarterly data:")
print(quarterly_data.head(12))

# Interpolate between quarters to create monthly values
# Fiscal year: Apr 2025 - Mar 2026
monthly_records = []

fiscal_months = [
    ('2025-04', 4),  # Apr
    ('2025-05', 5),  # May
    ('2025-06', 6),  # Jun
    ('2025-07', 7),  # Jul
    ('2025-08', 8),  # Aug
    ('2025-09', 9),  # Sep
    ('2025-10', 10), # Oct
    ('2025-11', 11), # Nov
    ('2025-12', 12), # Dec
    ('2026-01', 1),  # Jan
    ('2026-02', 2),  # Feb
    ('2026-03', 3),  # Mar
]

# Quarter mapping: month -> quarter end date
quarter_months = {
    3: pd.Timestamp('2025-03-31'),   # Q1 ends Mar 31
    4: pd.Timestamp('2025-03-31'),   # Apr in Q1
    5: pd.Timestamp('2025-03-31'),   # May in Q1
    6: pd.Timestamp('2025-06-30'),   # Q2 ends Jun 30
    7: pd.Timestamp('2025-06-30'),   # Jul in Q2
    8: pd.Timestamp('2025-06-30'),   # Aug in Q2
    9: pd.Timestamp('2025-09-30'),   # Q3 ends Sep 30
    10: pd.Timestamp('2025-09-30'),  # Oct in Q3
    11: pd.Timestamp('2025-09-30'),  # Nov in Q3
    12: pd.Timestamp('2025-12-31'),  # Q4 ends Dec 31
    1: pd.Timestamp('2025-12-31'),   # Jan in Q4
    2: pd.Timestamp('2025-12-31'),   # Feb in Q4
    3: pd.Timestamp('2026-03-31'),   # Mar in Q1 (next year) - but we'll use Q4 2025
}

# For interpolation:
# Q1 2025 (Mar 31) -> Q2 2025 (Jun 30): Apr, May
# Q2 2025 (Jun 30) -> Q3 2025 (Sep 30): Jul, Aug
# Q3 2025 (Sep 30) -> Q4 2025 (Dec 31): Oct, Nov
# Q4 2025 (Dec 31) -> ?: Jan, Feb, Mar (hold Q4 value or assume same as Q4)

quarter_ends = [
    pd.Timestamp('2025-03-31'),  # Q1
    pd.Timestamp('2025-06-30'),  # Q2
    pd.Timestamp('2025-09-30'),  # Q3
    pd.Timestamp('2025-12-31'),  # Q4
]

for dno in sorted(quarterly_data['DNO'].unique()):
    for ev_type in sorted(quarterly_data['EV_Type'].unique()):
        subset = quarterly_data[(quarterly_data['DNO'] == dno) & (quarterly_data['EV_Type'] == ev_type)]
        subset = subset.sort_values('period_date')

        if len(subset) < 2:
            continue

        # Create lookup: date -> value
        values_by_date = dict(zip(subset['period_date'], subset['install_count']))

        # Interpolate for each fiscal month
        for month_str, month_num in fiscal_months:
            # Determine which quarters to interpolate between
            if month_num in [4, 5]:  # Apr, May -> between Q1 and Q2
                q_start = pd.Timestamp('2025-03-31')
                q_end = pd.Timestamp('2025-06-30')
                progress = (month_num - 3) / 3.0  # 1/3 for Apr, 2/3 for May
            elif month_num in [7, 8]:  # Jul, Aug -> between Q2 and Q3
                q_start = pd.Timestamp('2025-06-30')
                q_end = pd.Timestamp('2025-09-30')
                progress = (month_num - 6) / 3.0  # 1/3 for Jul, 2/3 for Aug
            elif month_num in [10, 11]:  # Oct, Nov -> between Q3 and Q4
                q_start = pd.Timestamp('2025-09-30')
                q_end = pd.Timestamp('2025-12-31')
                progress = (month_num - 9) / 3.0  # 1/3 for Oct, 2/3 for Nov
            elif month_num in [1, 2, 3]:  # Jan, Feb, Mar -> hold Q4 value (no Q1 2026 data)
                q_start = pd.Timestamp('2025-12-31')
                q_end = pd.Timestamp('2025-12-31')
                progress = 0.0  # Use Q4 value
            elif month_num == 6:  # Jun -> use Q2 value exactly
                q_start = pd.Timestamp('2025-06-30')
                q_end = pd.Timestamp('2025-06-30')
                progress = 0.0
            elif month_num == 9:  # Sep -> use Q3 value exactly
                q_start = pd.Timestamp('2025-09-30')
                q_end = pd.Timestamp('2025-09-30')
                progress = 0.0
            elif month_num == 12:  # Dec -> use Q4 value exactly
                q_start = pd.Timestamp('2025-12-31')
                q_end = pd.Timestamp('2025-12-31')
                progress = 0.0
            elif month_num == 3:  # Mar -> use Q1 value exactly
                q_start = pd.Timestamp('2025-03-31')
                q_end = pd.Timestamp('2025-03-31')
                progress = 0.0

            # Interpolate
            val_start = values_by_date.get(q_start, 0)
            val_end = values_by_date.get(q_end, 0)
            interpolated = val_start + (val_end - val_start) * progress

            monthly_records.append({
                'period': month_str,
                'DNO': dno,
                'EV_Type': ev_type,
                'install_count': int(round(interpolated)),
                'tech_type': 'EV',
                'total_kw': 0.0
            })

df_monthly = pd.DataFrame(monthly_records)

print(f"\nMonthly interpolated records: {len(df_monthly)}")
print(f"\nSample monthly data:")
print(df_monthly.head(24))

print(f"\n\nMonthly EV Stock Summary:")
for ev_type in sorted(df_monthly['EV_Type'].unique()):
    type_data = df_monthly[df_monthly['EV_Type'] == ev_type]
    total = type_data['install_count'].sum()
    avg = type_data.groupby('period')['install_count'].sum().mean()
    print(f"  {ev_type}: Total {total:,} (Avg per month: {int(avg):,})")

# Save
out_path = os.path.join(OUTPUT_DIR, "ev_actuals_dno.csv")
df_monthly[['period', 'tech_type', 'DNO', 'EV_Type', 'install_count', 'total_kw']].to_csv(out_path, index=False)

print(f"\nEV Actuals (DNO level, interpolated): {out_path}")
print(f"Total records: {len(df_monthly)}")

print("\n" + "=" * 70)
print("Done. Monthly data with linear interpolation ready for dashboard.")
