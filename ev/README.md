# Data Directory

This directory contains the DfT/DVLA vehicle registration tables required for the EV stock reconstruction pipeline.

## Required Datasets

You need to download the following four CSV files from the Department for Transport (DfT):

### VEH0135: Licensed ultra low emission vehicles (ULEVs) by LSOA
- **Description**: BEV & PHEV counts by LSOA (all body types)
- **DfT Table**: VEH0135 - Licensed ultra low emission vehicles (ULEVs) at the end of the quarter by fuel type, keepership, and LSOA
- **Required columns**: LSOA code, Local Authority code, BEV count, PHEV count

### VEH0125: Vehicles by body type and LSOA
- **Description**: Total vehicles by body type and LSOA
- **DfT Table**: VEH0125 - Vehicles at the end of the quarter by licence status, body type, keepership, and LSOA
- **Required columns**: LSOA code, Local Authority code, Cars count, Other body types count

### VEH0105: Licensed vehicles by body type and local authority
- **Description**: Proportions of "other body types" that are vans by Local Authority
- **DfT Table**: VEH0105 - Licensed vehicles at the end of the quarter by body type, fuel type, keepership, and local authority
- **Required columns**: Local Authority code, proportion of other body types that are vans (or counts from which to compute)

### VEH0142: Licensed plug-in vehicles by local authority
- **Description**: Proportions of BEVs and PHEVs that are cars vs vans by Local Authority
- **DfT Table**: VEH0142 - Licensed plug-in vehicles at the end of the quarter by fuel type and local authority
- **Required columns**: Local Authority code, BEV/PHEV proportions or counts by body type

## Where to Download

1. **Visit the DfT Vehicle Licensing Statistics page**:
   - Main page: https://www.gov.uk/government/statistical-data-sets/vehicle-licensing-statistics-data-tables
   - Or search for "DfT vehicle licensing statistics VEH tables"

2. **Locate the specific tables**:
   - Look for tables with codes VEH0135, VEH0125, VEH0105, and VEH0142
   - These may be organized by year/quarter - download the most recent complete dataset
   - Each table should be available as a CSV download

3. **Alternative sources**:
   - DfT Statistics API (if available)
   - ONS or other UK government data portals

## File Organization

Place the downloaded CSV files directly in this `data/` directory:

```
data/
├── VEH0135.csv
├── VEH0125.csv
├── VEH0105.csv
└── VEH0142.csv
```

## File Naming

The package expects these exact filenames, or you can use any names and specify the full paths when running the CLI. For example:

```bash
ev-stock-lsoa \
  --veh0135 data/VEH0135.csv \
  --veh0125 data/VEH0125.csv \
  --veh0105 data/VEH0105.csv \
  --veh0142 data/VEH0142.csv \
  --output outputs/ev_stock_by_lsoa.csv
```

If your files have different names (e.g., `VEH0135_2023Q4.csv`), just use the full filename in the CLI arguments.

## Column Name Mapping

After downloading, you may need to update the column name mappings in `src/ev_stock/config.py` if the actual DfT column names differ from the default assumptions. See the main README.md for details.



