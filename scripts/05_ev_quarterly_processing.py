#!/usr/bin/env python3
"""
EV Quarterly Processing for Dashboard with Ofgem RIIO-ED2 Redistribution + London Adjustment

Locked Ofgem methodology:
- Uses ALL BEV+PHEV vehicles (Keepership=Total)
- Retains up to 100 per LSOA
- Redistributes excess based on <100 LSOA vehicle distribution
- Preserves BEV and PHEV exactly across national and DNO aggregates
- Works for ANY reporting quarter (not hardcoded)

Provisional London taxi/PHV adjustment (AFTER Ofgem):
- Applies only to LPN within UKPN scope
- Adjusts LPN stock such that LPN represents 25% of final adjusted UKPN
- Intended to account for London taxi/private-hire activity potentially underrepresented by registered-keeper geography
- NOT a nationally conserving redistribution
- NOT an Ofgem requirement
- Configurable parameter allows replacement when stronger taxi/PHV evidence is available

Processing pipeline:
1. Raw DVLA EV stock (df_VEH0135.csv)
2. Ofgem fleet-registration redistribution (locked methodology)
3. Post-Ofgem UKPN EV actual (auditable intermediate result)
4. London taxi/PHV spatial adjustment (provisional modelling assumption)
5. Final dashboard EV actual

Processes: Q1 2025, Q2 2025, Q3 2025, Q4 2025, Q1 2026, and future quarters as released
"""

import os
import re
import pandas as pd
import numpy as np
from fractions import Fraction

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EV_DIR = os.path.join(PROJECT_ROOT, "ev")
LOOKUPS_DIR = os.path.join(PROJECT_ROOT, "lookups")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "project", "output_processed")

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
    """Map fuel type to standard EV category.

    BEV: Battery electric vehicles
    PHEV: Plug-in hybrid electric vehicles ONLY (not non-plug-in hybrids)
    """
    if pd.isna(fuel_str):
        return None
    fuel = str(fuel_str).upper().strip()
    if "BATTERY" in fuel or "BEV" in fuel:
        return "BEV"
    elif "PLUG" in fuel and "HYBRID" in fuel:
        return "PHEV"
    return None

def apply_ofgem_redistribution(df_total_by_lsoa_fuel, period_label):
    """
    Apply locked Ofgem RIIO-ED2 redistribution to ALL BEV+PHEV stock.

    INTEGERISATION GRAIN: LSOA-level
    Deterministic tie-break: ascending LSOA code

    Input: DataFrame with columns [LSOA21CD, EV_Type, ev_count]
           (from Keepership=Total only)
    Output: DataFrame with same columns but adjusted ev_count after redistribution

    Locked methodology:
    1. Retain up to 100 per LSOA
    2. Pool excess from >100 LSOAs only
    3. Redistribute to <100 LSOAs by their vehicle share
    4. Preserve BEV and PHEV exactly
    """

    print(f"\n  Applying Ofgem redistribution to {period_label}...")

    # Convert to wide format (LSOA x EV_Type)
    df_wide = df_total_by_lsoa_fuel.pivot_table(
        index='LSOA21CD',
        columns='EV_Type',
        values='ev_count',
        fill_value=0
    )

    if 'BEV' not in df_wide.columns:
        df_wide['BEV'] = 0
    if 'PHEV' not in df_wide.columns:
        df_wide['PHEV'] = 0

    df_wide['total'] = df_wide['BEV'] + df_wide['PHEV']
    df_wide = df_wide.reset_index()
    df_wide = df_wide.sort_values('LSOA21CD').reset_index(drop=True)

    # === STEP 1: CATEGORIZE BY THRESHOLD ===
    df_wide['threshold_category'] = df_wide['total'].apply(
        lambda x: '<100' if x < 100 else ('=100' if x == 100 else '>100')
    )

    below_100_mask = df_wide['threshold_category'] == '<100'
    equal_100_mask = df_wide['threshold_category'] == '=100'
    above_100_mask = df_wide['threshold_category'] == '>100'

    # === STEP 2: RETENTION (up to 100 per LSOA) ===
    df_wide['retained_total'] = df_wide['total'].apply(lambda x: min(x, 100))

    # For BEV retention in >100 LSOAs: proportional split with largest-remainder
    df_wide['retained_bev'] = 0
    df_wide['retained_phev'] = 0

    for idx in df_wide[above_100_mask].index:
        observed_bev = int(df_wide.loc[idx, 'BEV'])
        observed_phev = int(df_wide.loc[idx, 'PHEV'])
        total = int(df_wide.loc[idx, 'total'])

        # Proportional split of 100
        bev_fraction = Fraction(observed_bev, total)
        phev_fraction = Fraction(observed_phev, total)

        retained_bev_exact = bev_fraction * 100
        retained_phev_exact = phev_fraction * 100

        # Integer allocation with largest-remainder
        retained_bev_base = int(retained_bev_exact)
        retained_phev_base = int(retained_phev_exact)

        total_base = retained_bev_base + retained_phev_base
        if total_base == 100:
            retained_bev = retained_bev_base
            retained_phev = retained_phev_base
        else:
            # Should be 99, add 1 to the one with larger remainder
            bev_remainder = retained_bev_exact - retained_bev_base
            phev_remainder = retained_phev_exact - retained_phev_base

            if bev_remainder >= phev_remainder:
                retained_bev = retained_bev_base + 1
                retained_phev = retained_phev_base
            else:
                retained_bev = retained_bev_base
                retained_phev = retained_phev_base + 1

        df_wide.loc[idx, 'retained_bev'] = retained_bev
        df_wide.loc[idx, 'retained_phev'] = retained_phev

    # For <100 and =100 LSOAs: retain all
    df_wide.loc[below_100_mask | equal_100_mask, 'retained_bev'] = df_wide.loc[below_100_mask | equal_100_mask, 'BEV']
    df_wide.loc[below_100_mask | equal_100_mask, 'retained_phev'] = df_wide.loc[below_100_mask | equal_100_mask, 'PHEV']

    # === STEP 3: EXCESS CALCULATION ===
    df_wide['excess_total'] = df_wide['total'] - df_wide['retained_total']
    df_wide['excess_bev'] = df_wide['BEV'] - df_wide['retained_bev']
    df_wide['excess_phev'] = df_wide['PHEV'] - df_wide['retained_phev']

    national_retained_bev = df_wide['retained_bev'].sum()
    national_retained_phev = df_wide['retained_phev'].sum()
    national_excess_bev = df_wide['excess_bev'].sum()
    national_excess_phev = df_wide['excess_phev'].sum()
    national_excess_total = df_wide['excess_total'].sum()

    # === STEP 4: REDISTRIBUTION WEIGHTS (based on <100 LSOAs) ===
    weight_denominator = df_wide[below_100_mask]['total'].sum()

    if weight_denominator == 0:
        raise ValueError("No vehicles in <100 LSOAs; cannot calculate redistribution weights")

    df_wide['weight'] = 0.0
    df_wide.loc[below_100_mask, 'weight'] = df_wide.loc[below_100_mask, 'total'] / weight_denominator

    # === STEP 5: ALLOCATE EXCESS BEV AND PHEV ===
    # Use exact fractions for ideal allocation
    df_wide['ideal_received_bev'] = 0.0
    df_wide['ideal_received_phev'] = 0.0

    df_wide['ideal_received_bev'] = df_wide['weight'] * national_excess_bev
    df_wide['ideal_received_phev'] = df_wide['weight'] * national_excess_phev

    # Integer allocation with largest-remainder (by LSOA)
    # BEV allocation
    bev_bases = df_wide['ideal_received_bev'].astype(int).values
    bev_remainders = df_wide['ideal_received_bev'].values - bev_bases
    bev_to_allocate = national_excess_bev - bev_bases.sum()

    # Sort by remainder (descending), then by LSOA code (ascending)
    allocation_order = sorted(
        range(len(df_wide)),
        key=lambda i: (-bev_remainders[i], df_wide.iloc[i]['LSOA21CD'])
    )

    bev_allocations = np.zeros(len(df_wide), dtype=int)
    for i in allocation_order[:int(bev_to_allocate)]:
        bev_allocations[i] = 1

    received_bev = bev_bases + bev_allocations

    # PHEV allocation
    phev_bases = df_wide['ideal_received_phev'].astype(int).values
    phev_remainders = df_wide['ideal_received_phev'].values - phev_bases
    phev_to_allocate = national_excess_phev - phev_bases.sum()

    allocation_order = sorted(
        range(len(df_wide)),
        key=lambda i: (-phev_remainders[i], df_wide.iloc[i]['LSOA21CD'])
    )

    phev_allocations = np.zeros(len(df_wide), dtype=int)
    for i in allocation_order[:int(phev_to_allocate)]:
        phev_allocations[i] = 1

    received_phev = phev_bases + phev_allocations

    df_wide['received_bev'] = received_bev
    df_wide['received_phev'] = received_phev
    df_wide['received_total'] = received_bev + received_phev

    # === STEP 6: RECOMBINE RETAINED + RECEIVED ===
    df_wide['adjusted_bev'] = df_wide['retained_bev'] + df_wide['received_bev']
    df_wide['adjusted_phev'] = df_wide['retained_phev'] + df_wide['received_phev']
    df_wide['adjusted_total'] = df_wide['adjusted_bev'] + df_wide['adjusted_phev']

    # === VALIDATION ===
    national_raw_bev = df_wide['BEV'].sum()
    national_raw_phev = df_wide['PHEV'].sum()
    national_raw_total = df_wide['total'].sum()
    national_adjusted_bev = df_wide['adjusted_bev'].sum()
    national_adjusted_phev = df_wide['adjusted_phev'].sum()
    national_adjusted_total = df_wide['adjusted_total'].sum()

    print(f"    Raw:      BEV={national_raw_bev:,}, PHEV={national_raw_phev:,}, Total={national_raw_total:,}")
    print(f"    Retained: BEV={national_retained_bev:,}, PHEV={national_retained_phev:,}, Total={national_retained_bev + national_retained_phev:,}")
    print(f"    Excess:   BEV={national_excess_bev:,}, PHEV={national_excess_phev:,}, Total={national_excess_total:,}")
    print(f"    Adjusted: BEV={national_adjusted_bev:,}, PHEV={national_adjusted_phev:,}, Total={national_adjusted_total:,}")

    # Check conservation
    bev_conservation = (national_adjusted_bev == national_raw_bev)
    phev_conservation = (national_adjusted_phev == national_raw_phev)
    total_conservation = (national_adjusted_total == national_raw_total)

    if not (bev_conservation and phev_conservation and total_conservation):
        print(f"    WARNING: Conservation check failed!")
        print(f"      BEV: {national_raw_bev:,} -> {national_adjusted_bev:,} (delta={national_adjusted_bev - national_raw_bev})")
        print(f"      PHEV: {national_raw_phev:,} -> {national_adjusted_phev:,} (delta={national_adjusted_phev - national_raw_phev})")
        print(f"      Total: {national_raw_total:,} -> {national_adjusted_total:,} (delta={national_adjusted_total - national_raw_total})")
        raise AssertionError(f"Conservation check failed for {period_label}")
    else:
        print(f"    [OK] Conservation verified")

    # Convert back to long format
    result_rows = []
    for _, row in df_wide.iterrows():
        result_rows.append({
            'LSOA21CD': row['LSOA21CD'],
            'EV_Type': 'BEV',
            'ev_count': int(row['adjusted_bev'])
        })
        result_rows.append({
            'LSOA21CD': row['LSOA21CD'],
            'EV_Type': 'PHEV',
            'ev_count': int(row['adjusted_phev'])
        })

    return pd.DataFrame(result_rows)

def apply_london_adjustment(df_adjusted_with_dno, period_label, dno_lookup):
    """
    Apply London taxi/PHV spatial adjustment to post-Ofgem EV stock.

    PROVISIONAL METHODOLOGY (UPLIFT-ONLY):
    Ensure LPN EV stock meets or exceeds a configurable minimum share of post-Ofgem
    adjusted UKPN EV stock. This is intended to account for London taxi/private-hire
    activity potentially underrepresented by registered-keeper geography.

    CRITICAL PROPERTY: This is an UPLIFT-ONLY adjustment.
    - If post-Ofgem LPN share >= TARGET (default 25%), uplift = 0 (no change to LPN).
    - If post-Ofgem LPN share < TARGET, uplift LPN to exactly TARGET share.
    - NEVER reduces LPN EV stock (uplift is always >= 0).

    This is NOT a nationally conserving redistribution. It is a UKPN-specific modelling
    assumption. The uplift adds vehicles to UKPN without modelling a corresponding
    deduction from rest-of-GB. Therefore, final UKPN stock may exceed UKPN's share of
    the nationally conserved Ofgem dataset.

    NOT an Ofgem requirement.
    Configurable parameter allows replacement when stronger taxi/PHV evidence is available.

    Input: DataFrame with columns [LSOA21CD, EV_Type, ev_count, DNO]
           (post-Ofgem UKPN-filtered data)
    Output: DataFrame with same columns but adjusted ev_count for LPN LSOAs only

    Configuration:
    LPN_TARGET_UKPN_SHARE = 0.25  (minimum LPN share of adjusted UKPN, never enforced downward)
    """

    LPN_TARGET_UKPN_SHARE = 0.25

    print(f"\n  Applying London taxi/PHV adjustment to {period_label}...")

    # Convert to wide format for easier calculation
    df_wide = df_adjusted_with_dno.pivot_table(
        index='LSOA21CD',
        columns='EV_Type',
        values='ev_count',
        fill_value=0
    )

    if 'BEV' not in df_wide.columns:
        df_wide['BEV'] = 0
    if 'PHEV' not in df_wide.columns:
        df_wide['PHEV'] = 0

    df_wide['total'] = df_wide['BEV'] + df_wide['PHEV']
    df_wide = df_wide.reset_index()

    # Get DNO for each LSOA
    df_wide = pd.merge(df_wide, dno_lookup, left_on='LSOA21CD', right_index=True, how='left')

    # Calculate post-Ofgem totals by DNO
    dno_totals = df_wide.groupby('DNO')['total'].sum()

    lpn_ofgem = dno_totals.get('LPN', 0)
    ukpn_ofgem = dno_totals.get('EPN', 0) + dno_totals.get('LPN', 0) + dno_totals.get('SPN', 0)
    lpn_share_ofgem = lpn_ofgem / ukpn_ofgem if ukpn_ofgem > 0 else 0

    # Solve for uplift: (LPN + x) / (UKPN + x) = TARGET_SHARE
    # x = (TARGET_SHARE * UKPN - LPN) / (1 - TARGET_SHARE)
    # CRITICAL: uplift must be >= 0 (never reduce LPN)
    # Therefore: x = max(0, rounded_calculation)

    numerator = LPN_TARGET_UKPN_SHARE * ukpn_ofgem - lpn_ofgem
    denominator = 1 - LPN_TARGET_UKPN_SHARE
    uplift_exact = numerator / denominator
    uplift_total = max(0, int(round(uplift_exact)))  # CRITICAL: never negative

    print(f"    Post-Ofgem LPN: {lpn_ofgem:,}")
    print(f"    Post-Ofgem UKPN: {ukpn_ofgem:,}")
    print(f"    Post-Ofgem LPN share: {lpn_share_ofgem*100:.4f}%")
    print(f"    Target minimum LPN share: {LPN_TARGET_UKPN_SHARE*100:.4f}%")
    print(f"    Calculated uplift: {uplift_total:,}")

    # Calculate uplift split using LPN's BEV/PHEV composition
    lpn_data = df_wide[df_wide['DNO'] == 'LPN']
    lpn_bev_total = lpn_data['BEV'].sum()
    lpn_phev_total = lpn_data['PHEV'].sum()
    lpn_total = lpn_bev_total + lpn_phev_total

    if lpn_total > 0:
        bev_fraction = lpn_bev_total / lpn_total
        phev_fraction = lpn_phev_total / lpn_total
    else:
        bev_fraction = 0.5
        phev_fraction = 0.5

    uplift_bev_ideal = uplift_total * bev_fraction
    uplift_phev_ideal = uplift_total * phev_fraction

    # Deterministic rounding
    uplift_bev = int(uplift_bev_ideal)
    uplift_phev = uplift_total - uplift_bev

    print(f"    LPN BEV composition: {bev_fraction*100:.4f}%")
    print(f"    Uplift split: BEV={uplift_bev:,}, PHEV={uplift_phev:,}")

    # Allocate BEV uplift proportionally across LPN LSOAs (largest-remainder)
    lpn_bev_lsoas = lpn_data[['LSOA21CD', 'BEV']].copy()

    if lpn_bev_total > 0:
        lpn_bev_lsoas['bev_ideal'] = (lpn_bev_lsoas['BEV'] / lpn_bev_total) * uplift_bev
    else:
        lpn_bev_lsoas['bev_ideal'] = 0.0

    lpn_bev_lsoas['bev_base'] = lpn_bev_lsoas['bev_ideal'].astype(int)
    lpn_bev_lsoas['bev_remainder'] = lpn_bev_lsoas['bev_ideal'] - lpn_bev_lsoas['bev_base']

    bev_base_sum = int(lpn_bev_lsoas['bev_base'].sum())
    bev_remaining = uplift_bev - bev_base_sum

    if bev_remaining > 0:
        lpn_bev_sorted = lpn_bev_lsoas.sort_values(['bev_remainder', 'LSOA21CD'], ascending=[False, True])
        lpn_bev_lsoas['bev_addon'] = 0
        lpn_bev_lsoas.loc[lpn_bev_sorted.head(bev_remaining).index, 'bev_addon'] = 1
    else:
        lpn_bev_lsoas['bev_addon'] = 0

    lpn_bev_lsoas['bev_uplift'] = lpn_bev_lsoas['bev_base'] + lpn_bev_lsoas['bev_addon']

    # Allocate PHEV uplift proportionally across LPN LSOAs (largest-remainder)
    lpn_phev_lsoas = lpn_data[['LSOA21CD', 'PHEV']].copy()

    if lpn_phev_total > 0:
        lpn_phev_lsoas['phev_ideal'] = (lpn_phev_lsoas['PHEV'] / lpn_phev_total) * uplift_phev
    else:
        lpn_phev_lsoas['phev_ideal'] = 0.0

    lpn_phev_lsoas['phev_base'] = lpn_phev_lsoas['phev_ideal'].astype(int)
    lpn_phev_lsoas['phev_remainder'] = lpn_phev_lsoas['phev_ideal'] - lpn_phev_lsoas['phev_base']

    phev_base_sum = int(lpn_phev_lsoas['phev_base'].sum())
    phev_remaining = uplift_phev - phev_base_sum

    if phev_remaining > 0:
        lpn_phev_sorted = lpn_phev_lsoas.sort_values(['phev_remainder', 'LSOA21CD'], ascending=[False, True])
        lpn_phev_lsoas['phev_addon'] = 0
        lpn_phev_lsoas.loc[lpn_phev_sorted.head(phev_remaining).index, 'phev_addon'] = 1
    else:
        lpn_phev_lsoas['phev_addon'] = 0

    lpn_phev_lsoas['phev_uplift'] = lpn_phev_lsoas['phev_base'] + lpn_phev_lsoas['phev_addon']

    # Merge uplifts back to main dataframe
    df_wide = df_wide.merge(lpn_bev_lsoas[['LSOA21CD', 'bev_uplift']], on='LSOA21CD', how='left')
    df_wide = df_wide.merge(lpn_phev_lsoas[['LSOA21CD', 'phev_uplift']], on='LSOA21CD', how='left')

    df_wide['bev_uplift'] = df_wide['bev_uplift'].fillna(0).astype(int)
    df_wide['phev_uplift'] = df_wide['phev_uplift'].fillna(0).astype(int)

    # Apply uplifts to LPN only
    df_wide['BEV_adjusted'] = df_wide['BEV'].copy()
    df_wide['PHEV_adjusted'] = df_wide['PHEV'].copy()

    mask_lpn = df_wide['DNO'] == 'LPN'
    df_wide.loc[mask_lpn, 'BEV_adjusted'] = df_wide.loc[mask_lpn, 'BEV'] + df_wide.loc[mask_lpn, 'bev_uplift']
    df_wide.loc[mask_lpn, 'PHEV_adjusted'] = df_wide.loc[mask_lpn, 'PHEV'] + df_wide.loc[mask_lpn, 'phev_uplift']

    # Validation
    bev_uplift_sum = int(df_wide['bev_uplift'].sum())
    phev_uplift_sum = int(df_wide['phev_uplift'].sum())

    lpn_after_bev = int(df_wide.loc[mask_lpn, 'BEV_adjusted'].sum())
    lpn_after_phev = int(df_wide.loc[mask_lpn, 'PHEV_adjusted'].sum())
    lpn_after_total = lpn_after_bev + lpn_after_phev

    ukpn_after_total = (int(df_wide.loc[df_wide['DNO'] == 'EPN', 'BEV'].sum() + df_wide.loc[df_wide['DNO'] == 'EPN', 'PHEV'].sum()) +
                        lpn_after_total +
                        int(df_wide.loc[df_wide['DNO'] == 'SPN', 'BEV'].sum() + df_wide.loc[df_wide['DNO'] == 'SPN', 'PHEV'].sum()))

    lpn_share = lpn_after_total / ukpn_after_total if ukpn_after_total > 0 else 0

    print(f"    BEV uplifts sum: {bev_uplift_sum:,}")
    print(f"    PHEV uplifts sum: {phev_uplift_sum:,}")
    print(f"    LPN after adjustment: {lpn_after_total:,} (share: {lpn_share*100:.4f}%)")

    # Convert back to long format
    result_rows = []
    for _, row in df_wide.iterrows():
        result_rows.append({
            'LSOA21CD': row['LSOA21CD'],
            'EV_Type': 'BEV',
            'ev_count': int(row['BEV_adjusted'])
        })
        result_rows.append({
            'LSOA21CD': row['LSOA21CD'],
            'EV_Type': 'PHEV',
            'ev_count': int(row['PHEV_adjusted'])
        })

    return pd.DataFrame(result_rows)

# === MAIN ===

print("=" * 80)
print("EV Quarterly Processing (Ofgem RIIO-ED2 Redistribution)")
print("=" * 80)

# Load LSOA21 -> DNO mapping
print("\nLoading LSOA21 -> DNO mapping (canonical geography)...")
dno_lookup = pd.read_csv(
    os.path.join(LOOKUPS_DIR, "LSOA to DNO.csv"),
    encoding='utf-8-sig'
)
dno_lookup = dno_lookup[['LSOA21CD', 'Majority Licence area']].drop_duplicates()
dno_lookup.columns = ['LSOA21CD', 'DNO']
dno_lookup.set_index('LSOA21CD', inplace=True)
print(f"  Loaded {len(dno_lookup)} LSOA21s")

# Process VEH0135 (fleet by LSOA, fuel type, keepership)
print("\n--- Processing VEH0135 (Stock by LSOA & Fuel) ---")
veh0135_path = os.path.join(EV_DIR, "df_VEH0135.csv")

df = pd.read_csv(veh0135_path, low_memory=False)
print(f"  Loaded VEH0135: {len(df):,} rows")

# Map fuel type to EV category
df['EV_Type'] = df['Fuel'].apply(map_fuel_type)
df = df[df['EV_Type'].notna()].copy()

# Use Keepership=Total (Ofgem requirement)
print(f"  Filtering to Keepership=Total only (Ofgem requirement)...")
df = df[df['Keepership'] == 'Total'].copy()

# Use LSOA21CD if available, else map from LSOA11CD
if 'LSOA21CD' not in df.columns or df['LSOA21CD'].isna().all():
    print("  Mapping LSOA11CD -> LSOA21CD...")
    bridge_path = os.path.join(
        LOOKUPS_DIR,
        "LSOA_(2011)_to_LSOA_(2021)_to_Local_Authority_District_(2022)_Exact_Fit_Lookup_for_EW_(V3).csv"
    )
    if os.path.exists(bridge_path):
        bridge = pd.read_csv(bridge_path)
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

df['LSOA21CD'] = df['LSOA21CD'].astype(str).str.strip()

# Detect quarter columns
quarter_cols = detect_quarter_columns(df.columns.tolist())
print(f"  Detected quarters: {quarter_cols}")

# Process each quarter independently
agg_final = []

for q_col in sorted(quarter_cols):
    period = quarter_label_to_period(q_col)

    print(f"\n  Processing {q_col} ({period})...")

    # Extract data for this quarter (Keepership=Total only)
    df_period = df[['LSOA21CD', 'EV_Type', q_col]].copy()
    df_period.columns = ['LSOA21CD', 'EV_Type', 'ev_count_raw']
    df_period['ev_count'] = clean_numeric(df_period['ev_count_raw'])
    df_period = df_period[df_period['ev_count'] > 0].copy()

    if len(df_period) == 0:
        print(f"    No data for {period}")
        continue

    # Aggregate by LSOA and EV_Type
    period_agg = df_period.groupby(['LSOA21CD', 'EV_Type'])['ev_count'].sum().reset_index()

    # Apply Ofgem redistribution
    period_adjusted = apply_ofgem_redistribution(period_agg, f"{q_col} ({period})")
    period_adjusted['period'] = period

    agg_final.append(period_adjusted)

if not agg_final:
    raise ValueError("No quarters processed successfully")

# Combine all quarters
agg = pd.concat(agg_final, ignore_index=True)

# Add DNO using canonical geography lookup
agg = agg.merge(dno_lookup, left_on='LSOA21CD', right_index=True, how='left')

# Keep only those with DNO mapping
agg = agg[agg['DNO'].notna()].copy()

print(f"\nProcessed {len(agg_final)} quarters")

# Apply London taxi/PHV adjustment (provisional UKPN modelling assumption)
print("\n--- Applying London taxi/PHV spatial adjustment ---")
agg_adjusted_list = []
for period_val in sorted(agg['period'].unique()):
    period_data = agg[agg['period'] == period_val].copy()

    # Apply London adjustment for this period
    period_adjusted = apply_london_adjustment(period_data, f"period {period_val}", dno_lookup)
    period_adjusted['period'] = period_val
    agg_adjusted_list.append(period_adjusted)

agg = pd.concat(agg_adjusted_list, ignore_index=True)
agg = agg.merge(dno_lookup, left_on='LSOA21CD', right_index=True, how='left')

# Convert to dashboard format
agg['tech_type'] = 'EV'
agg['install_count'] = agg['ev_count']
agg['total_kw'] = 0.0

# Format period as YYYY-MM (for consistency with LCT data)
agg['period'] = pd.to_datetime(agg['period']).dt.to_period('M').astype(str)

output_cols = ['period', 'tech_type', 'LSOA21CD', 'DNO', 'install_count', 'total_kw', 'EV_Type']
agg_out = agg[output_cols].copy()
agg_out = agg_out.sort_values(['period', 'LSOA21CD', 'EV_Type'])

# Create DNO-level aggregation
agg_dno = agg_out.groupby(['period', 'tech_type', 'DNO']).agg({
    'install_count': 'sum',
    'total_kw': 'sum'
}).reset_index()

os.makedirs(OUTPUT_DIR, exist_ok=True)

# Save outputs
out_path_lsoa = os.path.join(OUTPUT_DIR, "ev_quarterly_lsoa.csv")
agg_out.to_csv(out_path_lsoa, index=False)
print(f"\nLSOA-level EV data (post-Ofgem + London adjustment): {out_path_lsoa}")

out_path_dno = os.path.join(OUTPUT_DIR, "ev_quarterly_dno.csv")
agg_dno.to_csv(out_path_dno, index=False)
print(f"DNO-level EV data (post-Ofgem + London adjustment): {out_path_dno}")

# Summary with Q1 2026 London adjustment validation
print(f"\n--- Summary (Final Dashboard EV Stock) ---")
print(f"Total records: {len(agg_out)}")

qa_table = []
for period in sorted(agg_out['period'].unique()):
    period_data = agg_out[agg_out['period'] == period]
    total_evs = period_data['install_count'].sum()

    # Calculate DNO breakdown
    dno_breakdown = period_data.groupby('DNO')['install_count'].sum()
    epn_val = dno_breakdown.get('EPN', 0)
    lpn_val = dno_breakdown.get('LPN', 0)
    spn_val = dno_breakdown.get('SPN', 0)
    ukpn_val = epn_val + lpn_val + spn_val
    lpn_share = (lpn_val / ukpn_val * 100) if ukpn_val > 0 else 0

    qa_table.append({
        'period': period,
        'EPN': epn_val,
        'LPN': lpn_val,
        'SPN': spn_val,
        'UKPN': ukpn_val,
        'LPN_share_%': lpn_share
    })

    print(f"  {period}: UKPN={ukpn_val:,} (EPN={epn_val:,}, LPN={lpn_val:,}, SPN={spn_val:,}) LPN_share={lpn_share:.4f}%")

# Detailed Q1 2026 validation if present
q1_2026_data = [row for row in qa_table if row['period'] == '2026-03']
if q1_2026_data:
    print(f"\n--- Q1 2026 Detailed Validation ---")
    q1 = q1_2026_data[0]
    print(f"  EPN: {q1['EPN']:,} (expected: 419,870) {'[PASS]' if q1['EPN'] == 419870 else '[FAIL]'}")
    print(f"  LPN: {q1['LPN']:,} (expected: 227,241) {'[PASS]' if q1['LPN'] == 227241 else '[FAIL]'}")
    print(f"  SPN: {q1['SPN']:,} (expected: 261,853) {'[PASS]' if q1['SPN'] == 261853 else '[FAIL]'}")
    print(f"  UKPN: {q1['UKPN']:,} (expected: 908,964) {'[PASS]' if q1['UKPN'] == 908964 else '[FAIL]'}")
    print(f"  LPN share: {q1['LPN_share_%']:.4f}% (expected: 25.0000%) {'[PASS]' if abs(q1['LPN_share_%'] - 25.0) < 0.01 else '[FAIL]'}")

print("\n" + "=" * 80)
print("Done.")

