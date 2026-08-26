#!/usr/bin/env python3
"""Quick diagnostic to check data structure and identify processing issues"""

import pandas as pd
import os
import glob

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LCT_DIR = os.path.join(PROJECT_ROOT, "lct")

print("=" * 70)
print("COLUMN DIAGNOSIS FOR LCT DATA SOURCES")
print("=" * 70)

# 1. LCT Register
print("\n1. LCT REGISTER")
print("-" * 70)
try:
    df = pd.read_csv(os.path.join(LCT_DIR, "LCT Register.csv"), nrows=1)
    print(f"Columns: {list(df.columns)}")
    print(f"Expected 'Status' column: {'Status' in df.columns}")
    print(f"Found columns: Type={('Type' in df.columns)}, Installation_Date={('Installation_Date' in df.columns)}, MPAN_Postcode={('MPAN_Postcode' in df.columns)}")
except Exception as e:
    print(f"ERROR: {e}")

# 2. Device-Report (SmartConnect)
print("\n2. DEVICE-REPORT (SmartConnect)")
print("-" * 70)
try:
    df = pd.read_csv(os.path.join(LCT_DIR, "Device-Report (2).csv"), nrows=1)
    print(f"Columns: {list(df.columns)}")
    print(f"Has 'Installation Type': {'Installation Type' in df.columns}")
    print(f"Has 'Technology Type': {'Technology Type' in df.columns}")
    print(f"Has 'Application Export kW': {'Application Export kW' in df.columns}")
    print(f"Has 'Export Capacity Total kW': {'Export Capacity Total kW' in df.columns}")
    print(f"Has 'Date created': {'Date created' in df.columns}")
    print(f"Has 'Application Date Created': {'Application Date Created' in df.columns}")
    print(f"Has 'Postcode': {'Postcode' in df.columns}")
    print(f"Has 'Actual Status': {'Actual Status' in df.columns}")
except Exception as e:
    print(f"ERROR: {e}")

# 3. MCS (sample one file)
print("\n3. MCS DATA (sample file)")
print("-" * 70)
try:
    mcs_files = glob.glob(os.path.join(LCT_DIR, "MCS", "*.csv"))
    if mcs_files:
        df = pd.read_csv(mcs_files[0], nrows=1)
        print(f"File: {os.path.basename(mcs_files[0])}")
        print(f"Columns: {list(df.columns)}")
    else:
        print("No MCS CSV files found")
except Exception as e:
    print(f"ERROR: {e}")

# 4. ECR >1MW
print("\n4. ECR >1MW")
print("-" * 70)
try:
    df = pd.read_csv(os.path.join(LCT_DIR, "ecr_large.csv"), nrows=1)
    print(f"Columns: {list(df.columns)}")
    print(f"Has 'Connection Status': {'Connection Status' in df.columns}")
    print(f"Has 'Date Connected': {'Date Connected' in df.columns}")
    print(f"Has 'Already connected Registered Capacity (MW)': {'Already connected Registered Capacity (MW)' in df.columns}")
    print(f"Has 'Energy Conversion Technology 1': {'Energy Conversion Technology 1' in df.columns}")
    print(f"Has 'Postcode': {'Postcode' in df.columns}")
except Exception as e:
    print(f"ERROR: {e}")

# 5. ECR <1MW
print("\n5. ECR <1MW")
print("-" * 70)
try:
    df = pd.read_csv(os.path.join(LCT_DIR, "ecr_small.csv"), nrows=1)
    print(f"Columns: {list(df.columns)}")
    print(f"Has 'Connection Status': {'Connection Status' in df.columns}")
    print(f"Has 'Date Connected': {'Date Connected' in df.columns}")
except Exception as e:
    print(f"ERROR: {e}")

# 6. ZapMap
print("\n6. ZAPMAP")
print("-" * 70)
try:
    df = pd.read_csv(os.path.join(LCT_DIR, "zapmap.csv"), nrows=1)
    print(f"Columns: {list(df.columns)}")
    print(f"Has 'postal_code': {'postal_code' in df.columns}")
    print(f"Has 'power_band_name': {'power_band_name' in df.columns}")
    print(f"Has 'connector_power_kw': {'connector_power_kw' in df.columns}")
    print(f"Has 'zapmap_connector_added_date': {'zapmap_connector_added_date' in df.columns}")
except Exception as e:
    print(f"ERROR: {e}")

print("\n" + "=" * 70)
