#!/usr/bin/env python3
"""
Prepare EV Actuals data at DNO level for dashboard comparison
Aggregates quarterly EV stock data by DNO and vehicle type (BEV/PHEV)
Note: Stock values stay constant within quarter, don't divide
"""

import os
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "project", "output_processed")

print("=" * 70)
print("EV Actuals - DNO Level Preparation")
print("=" * 70)

# Load EV quarterly LSOA-level data
ev_path = os.path.join(OUTPUT_DIR, "ev_quarterly_lsoa.csv")
df_ev = pd.read_csv(ev_path)

print(f"\nLoaded EV quarterly data: {len(df_ev)} records")
print(f"Columns: {df_ev.columns.tolist()}")
print(f"Periods: {sorted(df_ev['period'].unique())}")
print(f"EV Types: {sorted(df_ev['EV_Type'].unique())}")

# Map to fiscal year months (Apr 2025 - Mar 2026)
# Quarterly stock values apply to all 3 months in that quarter
def quarter_to_fiscal_months(period):
    """Convert quarter to fiscal year months"""
    if period == '2025-03':
        return ['2025-04', '2025-05', '2025-06']
    elif period == '2025-06':
        return ['2025-07', '2025-08', '2025-09']
    elif period == '2025-09':
        return ['2025-10', '2025-11', '2025-12']
    elif period == '2025-12':
        return ['2026-01', '2026-02', '2026-03']
    return []

# Aggregate by DNO, period, and EV type (BEV/PHEV)
dno_data = []

for (period, dno, ev_type), group in df_ev.groupby(['period', 'DNO', 'EV_Type']):
    total_ev = group['install_count'].sum()

    # Map to fiscal months
    fiscal_months = quarter_to_fiscal_months(period)

    # Use same stock value for all 3 months in quarter (don't divide)
    for month in fiscal_months:
        dno_data.append({
            'period': month,
            'tech_type': 'EV',
            'DNO': dno,
            'EV_Type': ev_type,
            'install_count': int(total_ev),  # No division - stock value
            'total_kw': 0.0
        })

df_dno = pd.DataFrame(dno_data)

print(f"\nAggregated to DNO level: {len(df_dno)} records")
print(f"\nSample data (first 12 rows):")
print(df_dno.head(12))

print(f"\n\nEV Stock Summary by Type:")
for ev_type in sorted(df_dno['EV_Type'].unique()):
    type_data = df_dno[df_dno['EV_Type'] == ev_type]
    total = type_data['install_count'].sum()
    avg = type_data.groupby('period')['install_count'].sum().mean()
    print(f"  {ev_type}: Total {total:,} (Avg per month: {int(avg):,})")

# Save
out_path = os.path.join(OUTPUT_DIR, "ev_actuals_dno.csv")
df_dno.to_csv(out_path, index=False)

print(f"\nEV Actuals (DNO level): {out_path}")
print(f"Total records: {len(df_dno)}")

print("\n" + "=" * 70)
print("Done. Supports filtering by BEV / PHEV / Combined.")
