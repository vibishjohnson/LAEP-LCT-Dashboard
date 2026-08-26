#!/usr/bin/env python3
"""Test if postcode lookup actually works for October/December MCS data"""

import pandas as pd
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Load FIRST 100K rows of postcode lookup to test
lookup_path = os.path.join(PROJECT_ROOT, "lookups", "Postcode-LSOA-LAD lookup.csv")
lookup = pd.read_csv(lookup_path, encoding='latin-1', nrows=100000, low_memory=False)

print(f"Loaded {len(lookup)} rows from postcode lookup")
print(f"Columns: {lookup.columns.tolist()[:5]}")
print(f"First postcode: {lookup.iloc[0, 0]}")
print(f"Sample postcodes: {lookup.iloc[:5, 0].tolist()}")

# Standardize postcodes in lookup
def std_pc(s):
    return str(s).upper().replace(" ", "").replace('"', '').strip()

lookup['pc_std'] = lookup.iloc[:, 0].apply(std_pc)

# Test postcodes from October MCS
test_postcodes = ['TN325SP', 'TR166NA', 'PR30PA', 'AB125YR', 'B782BN', 'CB75FW']

print(f"\nTesting {len(test_postcodes)} postcodes:")
for pc in test_postcodes:
    pc_std = std_pc(pc)
    matches = (lookup['pc_std'] == pc_std).sum()
    print(f"  {pc:10s} -> {pc_std:8s}: {matches} matches")

print(f"\nTotal unique standardized postcodes in loaded data: {lookup['pc_std'].nunique()}")
