#!/usr/bin/env python3
"""Convert Excel MCS files to CSV format"""

import pandas as pd
import glob
import os

MCS_DIR = "C:/Users/johns2v/desktop/LAEP LCT Dashboard/lct/MCS"

# Find all Excel files
excel_files = glob.glob(os.path.join(MCS_DIR, "*.xlsx")) + glob.glob(os.path.join(MCS_DIR, "*.xlsm"))

print(f"Found {len(excel_files)} Excel MCS files to convert")
print("=" * 70)

converted = 0
for excel_file in sorted(excel_files):
    try:
        filename = os.path.basename(excel_file)
        csv_file = excel_file.replace(".xlsx", ".csv").replace(".xlsm", ".csv")

        # Skip if CSV already exists
        if os.path.exists(csv_file):
            print(f"SKIP: {filename} (CSV already exists)")
            continue

        # Read Excel and convert to CSV
        print(f"Converting: {filename}...", end=" ")
        df = pd.read_excel(excel_file)
        df.to_csv(csv_file, index=False)

        rows = len(df)
        size_mb = os.path.getsize(csv_file) / (1024*1024)
        print(f"OK ({rows:,} rows, {size_mb:.1f} MB)")
        converted += 1

    except Exception as e:
        print(f"ERROR: {str(e)}")

print("=" * 70)
print(f"Converted {converted} files to CSV")
