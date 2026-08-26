#!/usr/bin/env python3
"""
EV Quarterly Processing for Dashboard
Extracts Q1 2025 - Q1 2026 data at LSOA level for comparison with LCT actuals
"""

import os
import re
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EV_DIR = os.path.join(PROJECT_ROOT, "ev")
LOOKUPS_DIR = os.path.join(PROJECT_ROOT, "lookups")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "project", "output_processed")

# Target periods (fiscal year: Apr 2025 - Mar 2026, but using quarter-ends)
TARGET_PERIODS = [
    "2025-03-31",  # Q1 2025 (baseline)
    "2025-06-30",  # Q2 2025
    "2025-09-30",  # Q3 2025
    "2025-12-31",  # Q4 2025
    "2026-03-31",  # Q1 2026
]

def detect_quarter_columns(columns):
    """Detect quarter columns like '2025 Q2'."""
    quarter_re = re.compile(r"^\d{4}\sQ[1-4]$")
    return [c for c in columns if quarter_re.match(str(c))]

def quarter_label_to_period(quarter_label):
    """Convert 'YYYY Qn' -> quarter-end date string."""
    year_str, q_str = quarter_label.split()
    year = int(year_str)
    q = int(q_str[1])
    month_day = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}[q]
    return f"{year}-{month_day}"

def clean_numeric(s):
    """Clean numeric series: remove [c], [x] markers"""
    s = s.astype(str).str.replace(",", "", regex=False).str.strip()
    s = s.replace({"[x]": np.nan, "[c]": np.nan})
    return pd.to_numeric(s, errors="coerce").fillna(0).astype(int)

def map_fuel_type(fuel_str):
    """Map fuel type to standard EV category"""
    if pd.isna(fuel_str):
        return None
    fuel = str(fuel_str).upper().strip()
    if "BATTERY" in fuel or "BEV" in fuel:
        return "BEV"
    elif "PHEV" in fuel or "HYBRID" in fuel:
        return "PHEV"
    return None

print("=" * 70)
print("EV Quarterly Processing (Dashboard)")
print("=" * 70)

# Load LSOA21 -> DNO mapping
print("\nLoading LSOA21 -> DNO mapping...")
dno_lookup = pd.read_csv(
    os.path.join(LOOKUPS_DIR, "LSOA to DNO.csv"),
    encoding='utf-8-sig'
)
dno_lookup = dno_lookup[['LSOA21CD', 'Majority Licence area']].drop_duplicates()
dno_lookup.columns = ['LSOA21CD', 'DNO']
dno_lookup.set_index('LSOA21CD', inplace=True)
print(f"  Loaded {len(dno_lookup)} LSOA21s")

# Process VEH0135 (fleet by LSOA, fuel type)
print("\n--- Processing VEH0135 (Stock by LSOA & Fuel) ---")
veh0135_path = os.path.join(EV_DIR, "df_VEH0135.csv")

df = pd.read_csv(veh0135_path)

# Map fuel type to EV category
df['EV_Type'] = df['Fuel'].apply(map_fuel_type)
df = df[df['EV_Type'].notna()].copy()

# Use LSOA21CD if available, else LSOA11CD
if 'LSOA21CD' in df.columns:
    df['LSOA21CD'] = df['LSOA21CD'].astype(str).str.strip()
else:
    # If LSOA11CD, need to map to LSOA21CD
    print("  Using LSOA11CD, mapping to LSOA21CD...")
    bridge_path = os.path.join(
        LOOKUPS_DIR,
        "LSOA_(2011)_to_LSOA_(2021)_to_Local_Authority_District_(2022)_Exact_Fit_Lookup_for_EW_(V3).csv"
    )
    if os.path.exists(bridge_path):
        bridge = pd.read_csv(bridge_path)
        # Find LSOA columns (case-insensitive)
        lsoa11_col = [c for c in bridge.columns if 'LSOA11' in c.upper()][0]
        lsoa21_col = [c for c in bridge.columns if 'LSOA21' in c.upper()][0]
        bridge = bridge[[lsoa11_col, lsoa21_col]].drop_duplicates()
        bridge.columns = ['LSOA11CD', 'LSOA21CD']
        bridge['LSOA11CD'] = bridge['LSOA11CD'].astype(str).str.strip()
        bridge['LSOA21CD'] = bridge['LSOA21CD'].astype(str).str.strip()

        df['LSOA11CD'] = df['LSOA11CD'].astype(str).str.strip()
        df = df.merge(bridge, on='LSOA11CD', how='left')
    else:
        raise FileNotFoundError(f"Cannot find LSOA11->LSOA21 bridge at {bridge_path}")

# Detect quarter columns
quarter_cols = detect_quarter_columns(df.columns.tolist())
print(f"  Found quarters: {quarter_cols}")

# Melt quarters to long format
rows = []
for q in quarter_cols:
    tmp = df[['LSOA21CD', 'EV_Type', q]].copy()
    tmp.columns = ['LSOA21CD', 'EV_Type', 'ev_count_raw']
    tmp['period'] = quarter_label_to_period(q)
    rows.append(tmp)

df_long = pd.concat(rows, ignore_index=True)
df_long['ev_count'] = clean_numeric(df_long['ev_count_raw'])

# Filter to target periods
df_long = df_long[df_long['period'].isin(TARGET_PERIODS)].copy()

# Aggregate by LSOA21CD, period, EV_Type
agg = df_long.groupby(['period', 'LSOA21CD', 'EV_Type'])['ev_count'].sum().reset_index()

# Add DNO
agg = agg.merge(dno_lookup, left_on='LSOA21CD', right_index=True, how='left')

# Convert to dashboard format
# tech_type = EV, install_count = vehicle count, total_kw = 0 (no capacity data)
agg['tech_type'] = 'EV'
agg['install_count'] = agg['ev_count']
agg['total_kw'] = 0.0

# Format period as YYYY-MM (for consistency with LCT data)
agg['period'] = pd.to_datetime(agg['period']).dt.to_period('M').astype(str)

output_cols = ['period', 'tech_type', 'LSOA21CD', 'DNO', 'install_count', 'total_kw', 'EV_Type']
agg_out = agg[output_cols].copy()
agg_out = agg_out.sort_values(['period', 'LSOA21CD', 'EV_Type'])

# Also create DNO-level aggregation
agg_dno = agg_out.groupby(['period', 'tech_type', 'DNO']).agg({
    'install_count': 'sum',
    'total_kw': 'sum'
}).reset_index()

os.makedirs(OUTPUT_DIR, exist_ok=True)

# Save outputs
out_path_lsoa = os.path.join(OUTPUT_DIR, "ev_quarterly_lsoa.csv")
agg_out.to_csv(out_path_lsoa, index=False)
print(f"\nLSOA-level EV data: {out_path_lsoa}")

out_path_dno = os.path.join(OUTPUT_DIR, "ev_quarterly_dno.csv")
agg_dno.to_csv(out_path_dno, index=False)
print(f"DNO-level EV data: {out_path_dno}")

# Summary
print(f"\n--- Summary ---")
print(f"Total EV records: {len(agg_out)}")
for period in sorted(agg_out['period'].unique()):
    period_data = agg_out[agg_out['period'] == period]
    total_evs = period_data['install_count'].sum()
    print(f"  {period}: {total_evs:,} BEV/PHEV")

print("\n" + "=" * 70)
print("Done.")
