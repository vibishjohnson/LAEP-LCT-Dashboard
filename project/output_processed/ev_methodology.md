# EV Processing Methodology (Current)

## Scope

This process produces **BEV** and **PHEV** counts by vehicle body type at:

1. **LSOA21 level**
2. **LAEP level**
3. **UKPN licence area level** (`EPN`, `LPN`, `SPN`)

## Data Sources

- **`df_VEH0135.csv`** (EV input)
  - Geography: LSOA (current extract uses `LSOA21CD`; legacy extracts may use `LSOA11CD`)
  - Content: vehicle counts by fuel type and quarter
  - Role: baseline EV totals by LSOA and fuel (BEV/PHEV)

- **`VEH0142.csv`** (EV input)
  - Geography: LAD (`ONS Code`)
  - Content: vehicle counts by body type, fuel type, keepership, quarter
  - Role: derive LAD-level body-type proportions for BEV/PHEV

- **`LSOA_(2011)_to_LSOA_(2021)_to_Local_Authority_District_(2022)_Exact_Fit_Lookup_for_EW_(V3).csv`**
  - Mapping: `LSOA11CD -> LSOA21CD, LAD22CD`
  - Role: harmonise LSOA vintages and attach LAD22

- **`LA-LAEP lookup.csv`** (or `lad22_to_laep.csv` if present)
  - Mapping: `LAD22CD (ID_CODE) -> LAEP`
  - Role: LAEP aggregation

- **`ev/LSOA mapping.xlsx`**
  - Mapping: `LSOA21CD -> Majority Licence area`
  - Role: UKPN aggregation (`EPN/LPN/SPN`)

## Processing Steps

### 1) Keepership rule (double-count prevention)

For each source with a `Keepership` column:

- If any row contains `TOTAL` (case-insensitive), use **only** those `TOTAL` rows.
- Otherwise, retain all keepership categories.

This ensures totals are not double-counted when both component and total rows exist.

### 2) Fuel selection and standardisation

From raw fuel categories:

- **BEV**: `Fuel == "BATTERY ELECTRIC"`
- **PHEV**: fuel text containing `"PLUG"` or `"HYBRID"` (excluding battery electric)

Only BEV/PHEV are carried forward.

### 3) Quarter handling

- Quarter columns are detected via regex: `^\d{4}\sQ[1-4]$`
- Quarter labels convert to quarter-end date:
  - `Q1 -> YYYY-03-31`
  - `Q2 -> YYYY-06-30`
  - `Q3 -> YYYY-09-30`
  - `Q4 -> YYYY-12-31`
- Run mode:
  - `FULL_SERIES=False`
  - `LAST_N_QUARTERS` controls how many latest quarters are processed

### 4) Value cleaning

For all quarter values:

- Remove commas
- Treat `[x]`, `[c]`, and other non-numeric values as missing
- Convert to numeric
- Fill missing with 0 after filtering

### 5) LSOA EV totals (VEH0135) + redistribution

For each `(period, Fuel)`:

1. Compute per-LSOA baseline:
   - `base = min(value, 100)`
2. Pool excess:
   - `pool = sum(max(value - 100, 0))`
3. Redistribute pool proportionally:
   - `weights = base / sum(base)`
   - `redistributed = base + pool * weights`
4. Round to integers using **largest remainder** while preserving group totals exactly.

Result: redistributed LSOA totals for `BEV_total` and `PHEV_total`.

### 6) LAD body-type proportions (VEH0142)

`VEH0142` is treated as **LAD-level** input (`ONS Code`).

1. Drop body type `Total`
2. Map body types to:
   - `Cars`
   - `Vans` (`Light goods vehicles`)
   - `HGV` (`Heavy goods vehicles`)
   - `Buses` (`Buses and coaches`)
   - `Motorcycles`
   - `Other` (`Other vehicles`)
3. Aggregate to `(LAD22CD, period, Fuel, BodyType)`
4. Compute within-group proportions:
   - `prop = value / sum(value across body types)` for each `(LAD22CD, period, Fuel)`
5. Renormalise to sum to 1 where total > 0

QA export: `lad_ev_bodytype_proportions.csv`

### 7) Allocate redistributed LSOA totals by body type

1. Attach `LSOA21CD` and `LAD22CD` to LSOA totals via bridge
2. Join LAD-level body-type proportions on:
   - `(LAD22CD, period, Fuel)`
3. Allocate:
   - `allocated = redistributed_total * prop`
4. Apply **largest remainder rounding** within each `(LSOA, period, Fuel)` so:
   - allocations are integers
   - sum across body types equals redistributed fuel total exactly

### 8) Aggregate outputs

- **LSOA output**: `lsoa_ev_bodytype.csv`
  - `period, LSOA11CD, LSOA21CD, LAD22CD, Fuel, BodyType, ev_count`
- **LAEP output**: `laep_ev_bodytype.csv`
  - map `LAD22CD -> LAEP`, aggregate by `(period, LAEP, Fuel, BodyType)`
- **UKPN output**: `ukpn_ev_bodytype.csv`
  - map `LSOA21CD -> UKPN` using `Majority Licence area`, aggregate by `(period, UKPN, Fuel, BodyType)`

### 9) Missing geography handling

Rows with missing allocations/mappings are written to:
- `ev_missing_geo.csv`

This includes cases where LAD/LAEP/UKPN mapping is unavailable.

## Notes / Assumptions

- Outputs include **all body types except Total**.
- BEV/PHEV totals are conserved through redistribution and allocation (subject to rows that cannot be mapped and are sent to missing-geo).
- UKPN geography is assigned by **majority licence area** from `LSOA mapping.xlsx`.
- No SMMT data is currently used in this implemented pipeline.
- No explicit "company car cap and reallocation" logic is used in this version; methodology is based on redistribution + LAD body-type proportional allocation.
