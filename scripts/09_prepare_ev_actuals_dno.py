#!/usr/bin/env python3
"""
Prepare EV Actuals data at DNO level for dashboard comparison
Aggregates quarterly EV stock data by DNO and vehicle type (BEV/PHEV)
Note: Quarterly cumulative stock is mapped to quarter-end month only.
Missing intervening months remain null (no actual observation).
"""

import os
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "project", "output_processed")

print("=" * 70)
print("EV Actuals - DNO Level Preparation (Quarterly Semantics)")
print("=" * 70)

# Load EV quarterly LSOA-level data
ev_path = os.path.join(OUTPUT_DIR, "ev_quarterly_lsoa.csv")
df_ev = pd.read_csv(ev_path)

print(f"\nLoaded EV quarterly data: {len(df_ev)} records")
print(f"Columns: {df_ev.columns.tolist()}")
print(f"Periods: {sorted(df_ev['period'].unique())}")
print(f"EV Types: {sorted(df_ev['EV_Type'].unique())}")

# Map quarters to quarter-end months only (not forward-filled)
# Quarterly cumulative stock is a point-in-time observation
def quarter_to_month(period):
    """Map quarter period to its quarter-end month

    Cumulative quarterly stock is observed at:
    - 31 Mar 2025 for period 2025-03
    - 30 Jun 2025 for period 2025-06
    - 30 Sep 2025 for period 2025-09
    - 31 Dec 2025 for period 2025-12
    - 31 Mar 2026 for period 2026-03

    It is NOT forward-filled into subsequent months.
    Missing months remain null (no actual observation).
    """
    return period  # Return the quarter period as-is (it represents the quarter-end month)

# Aggregate by DNO, period, and EV type (BEV/PHEV)
dno_data = []

for (period, dno, ev_type), group in df_ev.groupby(['period', 'DNO', 'EV_Type']):
    total_ev = group['install_count'].sum()

    # Map quarter to its quarter-end month (no forward-fill)
    quarter_month = quarter_to_month(period)

    # Single entry per quarter (cumulative stock at quarter-end)
    dno_data.append({
        'period': quarter_month,
        'tech_type': 'EV',
        'DNO': dno,
        'EV_Type': ev_type,
        'install_count': int(total_ev),  # Cumulative stock value at quarter-end
        'total_kw': 0.0
    })

df_dno = pd.DataFrame(dno_data).sort_values(['period', 'DNO', 'EV_Type'])

print(f"\nAggregated to DNO level: {len(df_dno)} records")
print(f"(One entry per quarter-end observation per DNO per vehicle type)")
print(f"\nSample data (first 6 rows):")
print(df_dno.head(6))

print(f"\n\nQuarterly Observation Periods:")
unique_periods = sorted(df_dno['period'].unique())
print(f"  {unique_periods}")

print(f"\n\nEV Stock by Type (at latest observation):")
latest_period = df_dno['period'].max()
for ev_type in sorted(df_dno['EV_Type'].unique()):
    latest_data = df_dno[(df_dno['period'] == latest_period) & (df_dno['EV_Type'] == ev_type)]
    total = latest_data['install_count'].sum()
    print(f"  {ev_type}: {total:,} vehicles (at {latest_period})")

# Save
out_path = os.path.join(OUTPUT_DIR, "ev_actuals_dno.csv")
df_dno.to_csv(out_path, index=False)

print(f"\nEV Actuals (DNO level): {out_path}")
print(f"Total records: {len(df_dno)}")
print(f"Data represents quarterly cumulative vehicle stock")
print(f"  - One observation per quarter-end month")
print(f"  - No forward-fill into intervening months")
print(f"  - Intervening months remain null (no actual observation)")

print("\n" + "=" * 70)
print("Done. Quarterly stock observations mapped to quarter-end months only.")
