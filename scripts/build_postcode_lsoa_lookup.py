#!/usr/bin/env python3
"""
Build postcode to LSOA21 lookup using spatial join
Uses geopandas to match postcode points to LSOA polygons
"""

import os
import geopandas as gpd
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOOKUPS_DIR = os.path.join(PROJECT_ROOT, "lookups")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "project", "output_processed")

print("=" * 70)
print("Building Postcode to LSOA21 Lookup (Spatial Join)")
print("=" * 70)

# Load postcode data
print("\n1. Loading postcode data (codepo_gb.gpkg)...")
postcode_path = os.path.join(LOOKUPS_DIR, "codepo_gb.gpkg")
try:
    postcodes = gpd.read_file(postcode_path)
    print(f"   Loaded: {len(postcodes)} postcodes")
    print(f"   Columns: {postcodes.columns.tolist()[:5]}...")
    print(f"   CRS: {postcodes.crs}")
except Exception as e:
    print(f"   ERROR: {e}")
    exit(1)

# Load LSOA data
print("\n2. Loading LSOA21 data (LSOA_2021_EW_BSC_V4.shp)...")
lsoa_path = os.path.join(LOOKUPS_DIR, "LSOA_2021_EW_BSC_V4.shp")
try:
    lsoa = gpd.read_file(lsoa_path)
    print(f"   Loaded: {len(lsoa)} LSOA21 areas")
    print(f"   Columns: {lsoa.columns.tolist()[:8]}...")
    print(f"   CRS: {lsoa.crs}")
except Exception as e:
    print(f"   ERROR: {e}")
    exit(1)

# Ensure both have the same CRS
if postcodes.crs != lsoa.crs:
    print(f"\n   WARNING: CRS mismatch!")
    print(f"   Postcode CRS: {postcodes.crs}")
    print(f"   LSOA CRS: {lsoa.crs}")
    print(f"   Reprojecting postcodes to match LSOA...")
    postcodes = postcodes.to_crs(lsoa.crs)

# Spatial join - match each postcode point to LSOA polygon
print("\n3. Performing spatial join (postcode -> LSOA21)...")
print("   This may take a few minutes...")

try:
    # Spatial join: for each postcode, find which LSOA contains it
    joined = gpd.sjoin(postcodes, lsoa, how='left', predicate='within')
    print(f"   Result: {len(joined)} postcodes matched")
    print(f"   With LSOA match: {joined.index_right.notna().sum()}")
except Exception as e:
    print(f"   ERROR: {e}")
    exit(1)

# Extract key columns
print("\n4. Extracting postcode and LSOA21CD...")

# Identify the postcode column (usually 'pcd' or 'postcode')
postcode_col = None
for col in ['postcode', 'pcd', 'Postcode', 'PCD']:
    if col in joined.columns:
        postcode_col = col
        break

if not postcode_col:
    print(f"   WARNING: Could not find postcode column. Available: {joined.columns.tolist()[:10]}")
    postcode_col = joined.columns[0]

# Identify the LSOA21CD column (usually 'LSOA21CD' or 'LSOA21_Code')
lsoa_col = None
for col in ['LSOA21CD', 'LSOA21_Code', 'lsoa21cd', 'LSOA21_NM']:
    if col in joined.columns:
        lsoa_col = col
        break

if not lsoa_col:
    print(f"   WARNING: Could not find LSOA21CD column. Available: {joined.columns.tolist()}")
    # Try to find it by pattern
    for col in joined.columns:
        if 'lsoa' in col.lower() and 'cd' in col.lower():
            lsoa_col = col
            break

print(f"   Postcode column: {postcode_col}")
print(f"   LSOA column: {lsoa_col}")

# Create lookup DataFrame
lookup = joined[[postcode_col, lsoa_col]].copy()
lookup.columns = ['postcode_raw', 'LSOA21CD']

# Standardize postcode (uppercase, no spaces)
lookup['postcode'] = lookup['postcode_raw'].str.upper().str.replace(" ", "", regex=False).str.strip()

# Filter for England only (LSOA21CD starts with E)
lookup_england = lookup[lookup['LSOA21CD'].notna() & lookup['LSOA21CD'].astype(str).str.startswith('E')].copy()
print(f"\n5. Filtering for England only (E01xxxxx)...")
print(f"   Total matches: {len(lookup)}")
print(f"   England only: {len(lookup_england)}")

# Remove duplicates - keep first occurrence
lookup_england = lookup_england.drop_duplicates(subset=['postcode'], keep='first')
print(f"   After deduplication: {len(lookup_england)} unique postcodes")

# Save to CSV
output_path = os.path.join(OUTPUT_DIR, "postcode_lsoa21_lookup_spatial.csv")
os.makedirs(OUTPUT_DIR, exist_ok=True)
lookup_england[['postcode', 'LSOA21CD']].to_csv(output_path, index=False)

print(f"\n6. Output saved:")
print(f"   {output_path}")
print(f"   Records: {len(lookup_england)}")

# Show sample
print(f"\n7. Sample records:")
print(lookup_england[['postcode', 'LSOA21CD']].head(10))

print("\n" + "=" * 70)
print("Done! Spatial lookup ready for use in main processing script.")
print("=" * 70)
