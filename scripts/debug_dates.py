import pandas as pd

for fname, month_str in [("UKPN Data for October 2025.csv", "2025-10"), ("UKPN Data for December 2025.csv", "2025-12")]:
    df = pd.read_csv(f"C:/Users/johns2v/desktop/LAEP LCT Dashboard/lct/MCS/{fname}", low_memory=False)

    df['date'] = pd.to_datetime(df['Commissioning Date'], errors='coerce')
    df['period'] = df['date'].dt.to_period('M').astype(str)

    print(f"\n=== {fname} ===")
    print(f"Date range: {df['date'].min()} to {df['date'].max()}")
    print(f"Period values:")
    print(df['period'].value_counts().sort_index())
    print(f"Looking for period: {month_str}")
    print(f"Matches: {(df['period'] == month_str).sum()}")
