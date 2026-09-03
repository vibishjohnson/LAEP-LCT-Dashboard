# LAEP LCT Dashboard — Business Methodology

This document describes the **business/data methodology** that the LAEP LCT Dashboard is intended to reproduce. It is separate from `CLAUDE.md`, which provides operational guidance for repository work.

The dashboard produces results at **LSOA21 (Lower Layer Super Output Area 2021)** geography, with higher-level aggregations (DNO/licence area, LAEP) derived from LSOA-level detail.

---

## SECTION 1 — LCT INSTALLATION METHODOLOGY

This section documents the methodology for Low Carbon Technology (LCT) installations across the following technology types:

- **Heat Pumps**
- **Solar PV / Distributed Generation**
- **Battery Storage**
- **EV Charging Points**

### A. Source Datasets

The dashboard ingests five data sources to cover LCT installation activity:

#### MCS (Microgeneration Certification Scheme) — Primary Source
- **Geography:** UKPN data supplied by UKPN to MCS
- **Coverage:** Monthly files from April 2024 onwards
- **Technologies:** Heat Pumps (primary; up to 70 kW), Solar PV, Battery Storage, Distributed Generation (primary; up to 50 kW)
- **Scope:** National coverage; UKPN eligibility determined via postcode spatial mapping
- **Role:** Primary/authoritative source for certified installations within stated capacity bands
- **Capacity fields:** `Total Installed Capacity` (parsed to kW)
- **Identifier:** MPAN (where available)
- **Note:** EV chargers in MCS data are not part of the authoritative EV charger methodology (see EV charger section); they may be included in MCS output but are not the intended domestic/private EV source

#### LCT Register — Secondary Source (MPAN-Deduplicated)
- **Geography:** Postcode-based; records are UKPN-native (100% EPN/SPN/LPN)
- **Coverage:** Consolidated register of all historic LCT Register notifications
- **Technologies:** Heat Pumps (secondary), Solar PV, Battery Storage, EV chargers (domestic/private)
- **Scope:** UKPN-only; every record has native DNO field (EPN/SPN/LPN)
- **Role:** Secondary source; cross-source deduplication against MCS via MPAN
- **Capacity fields:** `Generation_Rating` and `Import_Rating` (in kW); technology-specific field selection required (TO CONFIRM per technology)
- **Identifier:** MPAN (authoritative for network ownership)
- **Deduplication rule (Pipeline 1):** Records with MPAN appearing in MCS are excluded
- **Note for DG:** LCT Register contains Solar PV and Battery Storage records, but these are not part of the authoritative DG capacity-band methodology unless explicitly confirmed as supplementary

#### Smart Enquiries / Fuse Upgrade Requests — Supplementary Source
- **Geography:** Postcode-based; records are UKPN-native (Connect Direct applicants)
- **Coverage:** Recent G99 Connect Direct connection applications
- **Technologies:** Heat Pumps (supplementary for Heat Pumps), Solar PV, Battery Storage, EV chargers (supplementary domestic/private)
- **Scope:** UKPN-only
- **Role:** Supplementary source for Heat Pumps, EV chargers, and Distributed Generation
- **Identifier:** MPAN
- **Deduplication:** MPAN-based deduplication against MCS and LCT Register per technology
- **DATA STATUS:** AUTHORITATIVE METHODOLOGY SOURCE — DATA CURRENTLY UNAVAILABLE
- **Note:** This source is not out of scope; it is a required part of the original methodology but data files are not currently provided to the pipeline. Stage 2B canonical schema should be designed to accommodate these records when data becomes available.

#### Device-Report (Connect Direct) — Connect Direct Application Records
- **Geography:** Postcode-based; records are 100% UKPN-native (Connect Direct applicants)
- **Coverage:** Recent G99 Connect Direct connection applications
- **Technologies:** Heat Pumps, Solar PV, Battery Storage, EV Chargers
- **Scope:** UKPN-only; appears to be most recent/current-year applications
- **Role:** TO CONFIRM — relationship to Smart Enquiries / Fuse Upgrade Requests is unresolved; not included in original methodology without independent evidence
- **Identifier:** MPAN (where assigned at connection)
- **Deduplication:** TO CONFIRM — how to treat overlap with LCT Register and Smart Enquiries sources
- **Note:** Data exists and is loaded into Pipeline 2; methodology role requires clarification

#### ECR Large (>1 MW generation / Import capacity) — UKPN-Native Source
- **Geography:** Postcode-based; records are 100% UKPN-native
- **Coverage:** All UKPN G99 Equipment Connection Register entries >1 MW
- **Technologies:** Solar PV, Battery Storage (generation/export capacity)
- **Scope:** UKPN-only; every record has native Licence Area (EPN/SPN/LPN) field
- **Role:** Supplementary; fills large-capacity generation registrations
- **Capacity fields:** TO CONFIRM — exact column name and unit
- **Identifier:** Postcode + native licence area
- **Deduplication:** TO CONFIRM — Stage 2 integration and dedup rules not yet finalized

#### ECR Small (50 kW–1 MW generation / Import capacity) — UKPN-Native Source
- **Geography:** Postcode-based; records are 100% UKPN-native
- **Coverage:** All UKPN G99/G98 Equipment Connection Register entries between 50 kW and 1 MW
- **Technologies:** Solar PV, Battery Storage, Distributed Generation (50 kW–1 MW band)
- **Scope:** UKPN-only; every record has native Licence Area field (note: trailing space in column name)
- **Role:** Supplementary for Distributed Generation; captures installations in the 50 kW–1 MW band
- **Capacity fields:** TO CONFIRM — exact field selection and conversions, missing data handling
- **Deduplication:** TO CONFIRM — Stage 2 integration and dedup rules not yet finalized

#### ZapMap — Public EV Charging Supplement
- **Geography:** Public charging locations; postcode-based
- **Coverage:** National public EV charging infrastructure locations
- **Technologies:** EV Charging Points (public)
- **Scope:** National; UKPN eligibility determined via postcode spatial mapping
- **Role:** Supplementary for public charging; distinct from domestic/workplace chargers in LCT Register
- **Capacity fields:** TO CONFIRM — field interpretation and handling
- **Identifier:** Postcode (no MPAN; spatial allocation only)
- **Deduplication:** TO CONFIRM — relationship to LCT Register and public-vs-domestic dedup strategy

### B. UKPN Eligibility and Geography

The dashboard is **UKPN-scoped only**. Valid licence areas are: **EPN**, **SPN**, **LPN**.

#### Broad Sources (MCS, ZapMap)
These datasets cover the entire UK and are not pre-filtered to UKPN.

**UKPN eligibility determination:**
```
Postcode (from source record)
  → LSOA21CD (via postcode_lsoa21_lookup_spatial.csv)
  → Majority Licence area (via LSOA to DNO.csv lookup)
  → If Majority Licence area ∈ {EPN, SPN, LPN}: UKPN-eligible
  → Otherwise: Excluded as non-UKPN
```

**Geographic allocation:**
- Postcode is the starting point; if postcode cannot be standardized/normalized, record is rejected
- Postcode must appear in the lookup; otherwise, record is marked as unresolved
- LSOA21CD from lookup is authoritative for LSOA-level reporting
- Majority Licence area from LSOA lookup determines DNO assignment

**Note on spatial-native disagreements:** For UKPN-native sources (see below), a disagreement between native and spatial licence area does NOT exclude the record from UKPN scope; spatial provides allocation detail, not eligibility.

#### UKPN-Native Sources (LCT Register, ECR Large, ECR Small)
These datasets are **100% UKPN-scoped by definition**. Every record has an authoritative native DNO/Licence Area field (EPN/SPN/LPN).

**UKPN eligibility determination:**
```
Native DNO / Licence Area field (from source record)
  → Normalize to standard abbreviation (EPN/SPN/LPN)
  → If normalized value ∈ {EPN, SPN, LPN}: UKPN-eligible (100% of records)
  → Otherwise: Record is treated as data error (should not occur)
```

**Geographic allocation:**
```
Postcode (from source record)
  → LSOA21CD (via postcode_lsoa21_lookup_spatial.csv)
  → Majority Licence area (via LSOA to DNO.csv lookup)
```

- Postcode is used for LSOA assignment (geographic detail)
- Native DNO/Licence Area is the authoritative network assignment (already verified UKPN)
- If postcode cannot be resolved to LSOA:
  - Record remains UKPN-eligible (native field stands)
  - LSOA21CD is marked as unresolved / unavailable
  - Geographic allocation is incomplete but UKPN membership is confirmed
- If postcode resolves to LSOA but spatial Majority Licence area disagrees with native:
  - Disagreement is noted (rare boundary cases)
  - Record remains UKPN-eligible (native DNO is authoritative)
  - Spatial LSOA is used for reporting; disagreement is tracked as an audit flag (RESOLVED_SPATIAL_MISMATCH)
  - Documented patterns: 652 disagreements across LCT/ECR (0.11% of combined UKPN-native totals)

### C. Technology Classification

Source records are classified into dashboard technology types via pattern matching on technology name fields. Exact matching strategy varies by source.

#### Heat Pumps
- **MCS:** Match on `Technology Type` field for 'heat pump' (case-insensitive)
- **LCT Register:** Match on `Type` field for 'heat pump'
- **ECR Large/Small:** Match on technology type field for 'heat pump'
- **ZapMap:** Not applicable (ZapMap is EV-only)
- **Device-Report:** Match on `Technology Type` for 'Heat Pump'

#### Solar PV / Distributed Generation
- **MCS:** Match on `Technology Type` for 'solar pv', 'solar photovoltaic', 'solar keymark'
- **LCT Register:** Match on `Type` for solar variants
- **ECR Large/Small:** Match on technology fields for 'Solar PV', 'Distributed Generation'
- **ZapMap:** Not applicable
- **Device-Report:** Match for 'Solar PV'

#### Battery Storage
- **MCS:** Match on `Technology Type` for 'battery' or 'storage'
- **LCT Register:** Match on `Type` for battery variants
- **ECR Large/Small:** Match on technology fields for 'Battery Storage', 'Energy Storage'
- **ZapMap:** Not applicable
- **Device-Report:** Match for 'Battery Storage & Hybrid Inverters'

#### EV Charging Points
- **MCS:** Match on `Technology Type` for 'ev charging' or 'v2g'
- **LCT Register:** Match on `Type` for 'EV notification', 'EV charging' (Pipeline 1), or 'EV Charging' (Pipeline 2)
- **ECR Large/Small:** Match on technology fields for 'EV Charger', 'Electric Vehicle Charge Point'
- **ZapMap:** All records are EV charging points (public infrastructure)
- **Device-Report:** Match for 'Electric Vehicle Charge Point'

**Important distinction:** Technology classification is applied at record level. Unrecognized technology values result in record exclusion.

### D. Capacity Methodology

Capacity is recorded in kilowatts (kW) for LCT installations. Conversions and parsing vary by source.

#### MCS
- **Field:** `Total Installed Capacity` (mixed format: may include unit suffix)
- **Parsing:**
  - Extract numeric portion (e.g., '5.5 kW' → 5.5)
  - If unit is MW: multiply by 1,000 to convert to kW
  - If unit is W (but not kW): divide by 1,000 to convert to kW
  - If no unit specified: assume kW
- **Missing values:** Treated as 0.0 kW (installation recorded but capacity unknown)

#### LCT Register
- **Fields:** `Generation_Rating` (generation/export capacity, in kW), `Import_Rating` (import/consumption capacity, in kW)
- **Parsing:**
  - Both fields are retained; technology-specific field selection occurs at Stage 2 (TO CONFIRM)
  - Values expected to be pre-converted to kW
  - Missing values: Treated as 0.0 kW
- **CURRENT IMPLEMENTATION NOTE (Pipeline 1):** Takes maximum of the two fields, but this may not be appropriate for all technologies and should be verified against Stage 2 analysis

#### ECR Large
- **Field:** TO CONFIRM — exact column name, unit, and handling of multiple capacity fields

#### ECR Small
- **Field:** TO CONFIRM — technology-specific capacity fields and conversions

#### ZapMap
- **Field:** `connector_power_kw` (actual connector power in kW; category assumptions applied as fallback where power is missing)
- **CONFIRMED NEW DECISION:** Use actual connector_power_kw values where available; apply power_band_name category assumptions only when connector_power_kw is missing or invalid

#### Device-Report
- **Field:** TO CONFIRM — field selection strategy for multiple capacity-related columns

**Important:** Capacity is recorded per installation (one row = one device). Aggregation to installation count is via record count; aggregation to total capacity is via sum of capacity_kw.

### E. Source Chains and Deduplication

Source treatment is **technology-specific**, not universal. Different technologies may use different source precedence and deduplication rules.

#### Technology-Specific Source Chains (TO CONFIRM per technology)

**Heat Pumps:**
- Primary: MCS (certified installations ≤70 kW)
- Secondary: LCT Register
- Supplementary: Smart Enquiries / Fuse Upgrade Requests (if available)
- Supplementary: Device-Report (Connect Direct applications)
- **Deduplication:** MPAN-based against prior sources per technology

**Solar PV / Distributed Generation:**
- By capacity band:
  - 0–50 kW: MCS (primary), LCT Register (secondary)
  - 50 kW–1 MW: ECR Small (primary for this band), LCT Register (if not in ECR), MCS (if capacity ≤50 kW)
  - >1 MW: ECR Large (primary for this band)
- **Deduplication:** MPAN-based within capacity band; cross-band treatment TO CONFIRM

**Battery Storage:**
- By capacity band:
  - 0–50 kW: MCS (primary), LCT Register (secondary)
  - 50 kW–1 MW: ECR Small (primary for this band)
  - >1 MW: ECR Large (primary for this band)
- **Deduplication:** MPAN-based per capacity band; cross-band treatment TO CONFIRM

**EV Chargers:**
- Domestic/workplace/private installations:
  - Primary: LCT Register
  - Secondary: Smart Enquiries / Fuse Upgrade Requests (if available)
  - Supplementary: Device-Report (Connect Direct applications)
- Public installations:
  - ZapMap (primary/supplementary for public charging)
  - Supplementary: ECR Small (if registered as connection point)
- **Important:** MCS EV charger records are not part of the authoritative EV methodology; they are separate from LCT Register domestic chargers
- **Deduplication:** MPAN-based for domestic; spatial for public (distinct populations)

#### Pipeline 1 (Currently Active)
**Actual implementation:** MPAN-only cross-source deduplication between MCS and LCT Register only

```
1. Process MCS first (all technologies)
   - Collect all unique MPAN values
   
2. Process LCT Register second (all technologies)
   - Exclude any record where MPAN appears in MCS MPAN set
   
3. Combine MCS + deduplicated LCT Register
   - Aggregate by (period, tech_type, DNO)
   - Output: dashboard_data_dno.csv, dashboard_data_lsoa.csv
```

**Limitation:** Pipeline 1 does not implement technology-specific source chains; uses universal MCS→LCT Register precedence

**Current coverage (Pipeline 1):**
- MCS (all months, all technologies)
- LCT Register (MPAN-deduplicated subset)
- ECR Large, ECR Small, ZapMap: NOT INCLUDED in Pipeline 1

#### Pipeline 2 (New / Not Yet Production)
**Proposed deduplication logic:** Technology-specific chains with MPAN+Month+Technology keys (within source)

**Status:** TO CONFIRM — Stage 2B will finalize technology-specific chains and cross-source deduplication rules

**Benchmark totals (after Stage 1 geographic filtering, before Stage 2 dedup):**
- MCS: 32,318 raw records → 6,774 UKPN-eligible (spatial)
- LCT Register: 599,928 raw records → 599,928 UKPN-eligible (100% native UKPN)
- ECR Large: 1,213 raw records → 1,213 UKPN-eligible (100% native UKPN)
- ECR Small: 4,456 raw records → 4,456 UKPN-eligible (100% native UKPN)
- ZapMap: 122,690 raw records → 46,143 UKPN-eligible (spatial)

### F. LSOA Output

Retained records (UKPN-eligible) are aggregated at LSOA21 level for reporting.

#### LSOA21CD Assignment
- From postcode spatial lookup; LSOA21CD is authoritative for geographic detail
- If postcode cannot resolve: LSOA21CD is marked as unresolved
- For UKPN-native sources without successful postcode resolution: Record is retained for UKPN counts but may not contribute to LSOA-level reporting

#### Aggregation to LSOA21
```
Group by: (period, tech_type, LSOA21CD, DNO)
Aggregate:
  - install_count = number of retained records in group
  - total_kw = sum of capacity_kw in group
```

#### Missing LSOA Assignments
- Records with unresolved LSOA21CD are still included in DNO-level counts (via native field for UKPN-native sources)
- TO CONFIRM — Whether such records are excluded from LSOA-level output or allocated via fallback rule

#### Higher-Level Aggregations
- **DNO level:** Aggregate across LSOA21 within each DNO
- **LAEP level:** Aggregate via LAD22CD to LAEP mapping (if available)
- **National level:** Sum across all DNOs and LSOAs

---

## SECTION 2 — EV VEHICLE COUNT METHODOLOGY

This section documents the intended EV vehicle-count calculation, which is **separate from EV Charger installations** (documented in Section 1).

**Purpose:** Estimate BEV (Battery Electric Vehicle) and PHEV (Plug-in Hybrid Electric Vehicle) stock by vehicle body type at LSOA, LAEP, and UKPN levels.

### A. Source Datasets and Roles

#### VEH0135 — EV Stock by LSOA
- **Geography:** LSOA21CD (or legacy LSOA11CD in older extracts)
- **Content:** BEV and PHEV counts by quarter, aggregated across ALL body types (cars, vans, taxis, buses, HGVs, other vehicles)
- **Role (in EV car/van calculation):** Provides LSOA-level baseline for EV totals
- **Quarterly:** Yes; period labels converted to quarter-end dates (Q1→03-31, Q2→06-30, Q3→09-30, Q4→12-31)

#### VEH0125 — Total Vehicles by LSOA
- **Geography:** LSOA21CD
- **Content:** Total vehicle counts by body type (Cars, Motorcycles, Other body types)
- **Role (in total vehicle/van calculation):** Provides LSOA-level total vehicle baseline; "Other body types" are disaggregated using VEH0105 proportions
- **Quarterly:** Yes
- **Status:** REQUIRED BY METHODOLOGY but currently NOT IMPLEMENTED

#### VEH0105 — LA-Level Body-Type Proportions
- **Geography:** Local Authority level
- **Content:** All vehicles by body type, fuel type, and keepership; spans all vehicle types (not just EVs)
- **Role (in total vehicle/van calculation):** Provides LA-level proportion of "other body types" that are vans; applied to VEH0125 LSOA "other body types" to estimate LSOA van counts
- **Purpose:** Disaggregation of total "other body types" into vans vs. remaining other types
- **Quarterly:** Yes
- **Status:** REQUIRED BY METHODOLOGY but currently NOT IMPLEMENTED

#### VEH0142 — LA-Level EV Body-Type Proportions
- **Geography:** Local Authority level (ONS Code = LAD code)
- **Content:** BEV and PHEV counts by body type (Cars, Vans, HGVs, Buses, Motorcycles, Other), fuel type, and keepership
- **Role (in EV car/van calculation):** Provides LA-level proportions of BEVs and PHEVs that are cars vs. vans
- **Purpose:** Disaggregation of EV totals into BEV cars, BEV vans, PHEV cars, PHEV vans by LSOA (via LA mapping)
- **Quarterly:** Yes
- **Status:** IMPLEMENTED

### B. Intended Calculation Flow

The methodology unfolds in six sequential steps:

#### Step 1: Load and Filter VEH0135 (EV Baseline)

VEH0135 provides baseline BEV and PHEV counts by LSOA and quarter.

- **Keepership filter:** If "TOTAL" rows exist, retain only those rows (to avoid double-counting)
- **Fuel filter:** Extract BEV and PHEV only
- **Value cleaning:** Remove confidentiality markers ([x], [c])
- **Period selection:** Filter to target quarters (e.g., 2025 Q1 onwards)
- **Result:** EV counts by LSOA, period, and fuel type (BEV/PHEV)

**Current status:** ✅ IMPLEMENTED

---

#### Step 2: Load and Filter VEH0125 (Total Vehicle Baseline)

VEH0125 provides baseline total vehicle counts by LSOA, body type, and keepership.

- **Keepership filter:** Same rule as VEH0135
- **Body types retained:** Cars, Motorcycles, Other body types
- **Value cleaning:** Remove confidentiality markers
- **Period selection:** Same target quarters as Step 1
- **Result:** Total vehicle counts by LSOA, body type, and period

**Current status:** ❌ NOT IMPLEMENTED — VEH0125 is not loaded by any script

**Impact:** Cannot compute total vehicle baseline; cannot establish EV penetration ratio by LSOA

---

#### Step 3: Apply VEH0105 LA-Level Van Proportions

VEH0105 provides, at LA level, the proportion of "other body types" that are vans.

- **Computation:** For each (LA, quarter), proportion_vans = vans / (vans + other)
- **Application:** For each LSOA in that LA:
  ```
  vans_by_lsoa = VEH0125["Other body types"] × LA_level_van_proportion
  other_non_van = VEH0125["Other body types"] × (1 − LA_level_van_proportion)
  ```
- **Result:** LSOA-level estimate of total vans (separate from VEH0142 EV van calculation)

**Current status:** ❌ NOT IMPLEMENTED — VEH0105 is not loaded; "other body types" van disaggregation does not occur

**Impact:** No LSOA-level total van baseline; VEH0142 body-type allocation stands alone without total-vehicle context

**Note:** This calculation is **independent of Step 4** (EV body-type allocation via VEH0142). VEH0125+VEH0105 establish total-vehicle-side information; VEH0135+VEH0142 establish EV-side information.

---

#### Step 4: Apply VEH0142 LA-Level EV Body-Type Proportions

VEH0142 provides LA-level proportions of BEVs and PHEVs by body type.

- **Computation:** For each (LA, quarter, fuel type), proportions = count[body_type] / sum(all body types) for BEV/PHEV
- **Application:** For each LSOA in that LA:
  ```
  BEV_cars = VEH0135_BEV_total × LA_level_proportion[BEV, Cars]
  BEV_vans = VEH0135_BEV_total × LA_level_proportion[BEV, Vans]
  PHEV_cars = VEH0135_PHEV_total × LA_level_proportion[PHEV, Cars]
  PHEV_vans = VEH0135_PHEV_total × LA_level_proportion[PHEV, Vans]
  (plus HGVs, Buses, Motorcycles, Other from VEH0142)
  ```
- **Rounding:** Apply largest-remainder rounding within each (LSOA, period, fuel) group to preserve fuel totals exactly
- **Result:** EV counts by LSOA, fuel type, and body type

**Current status:** ✅ IMPLEMENTED (though in current code, this occurs without prior application of Steps 2–3)

---

#### Step 5: Apply Company-Car Adjustment

Company cars may be registered in locations that do not reflect where they are actually used. An adjustment redistributes company cars so that:

**Target:** The UKPN region has the same proportion of UK company cars as it does of UK private cars.

**Methodology:**

Using VEH0142 and VEH0105, establish at Local Authority level:
- Additional BEV cars needed
- Additional PHEV cars needed
- Additional total cars needed

These additional counts are then distributed from LA to LSOA according to the existing LSOA distributions of:
- BEVs (for additional BEV cars)
- PHEVs (for additional PHEV cars)
- Total cars (for additional total cars)

**Exact distribution formula:** TO CONFIRM — exact weights and methodology for LSOA allocation

**Current status:** ❌ NOT IMPLEMENTED — No company-car redistribution logic exists

**Impact:** UKPN regions may be systematically over/under-weighted for company vehicle representation if registration location ≠ actual use location

---

#### Step 6: Apply 100-Cap Redistribution

After company-car adjustment, redistribute to smooth LSOA concentration.

**Authoritative methodology:**
- Identify all LSOAs across Great Britain with more than 100 EVs
- Pool the amount above the 100-EV cap
- Redistribute the pooled quantity across LSOAs according to the agreed redistribution methodology
- Preserve the appropriate total

**Timing:** Applied AFTER company-car adjustment (Step 5)

**Scope:** All GB LSOAs (not filtered to UKPN)

**Current implementation:**
- Algorithm: `base = min(value, 100)`, `pool = sum(max(value - 100, 0))`, `redistributed = base + pool × (base / sum(base))`
- Rounding: Largest-remainder method
- Applied separately by period and fuel type (BEV/PHEV)
- Scope: All LSOAs within same (period, Fuel) group

**Exact redistribution weighting:** IMPLEMENTED — NEEDS METHODOLOGY VALIDATION (whether base-weighted proportions are the intended weights is not established)

**Current status:** ⚠️ PARTIALLY IMPLEMENTED — Algorithm is correct but occurs BEFORE company-car adjustment (which is missing), and weighting methodology is not validated

---

### C. Final Geographic Aggregation

After body-type allocation and 100-cap redistribution:

- **Attach geographic attributes:** Map LSOA21CD to LAD22CD (via bridge lookup) and Majority Licence Area (via LSOA-to-DNO lookup)
- **LAEP aggregation:** Aggregate by (period, LAEP, Fuel, BodyType) via LAD22CD → LAEP mapping
- **UKPN aggregation:** Aggregate by (period, UKPN, Fuel, BodyType) via LSOA21CD → Majority Licence Area
- **Missing geography:** Records with unresolved mappings written to separate file for audit

### D. Current Implementation Status

| Step | Intended | Implemented | Status |
|------|----------|---|---|
| 1. VEH0135 baseline | ✅ Yes | ✅ Yes | IMPLEMENTED — MATCHES METHODOLOGY |
| 2. VEH0125 total vehicles | ✅ Yes | ❌ No | IMPLEMENTATION GAP |
| 3. VEH0105 van proportions | ✅ Yes | ❌ No | IMPLEMENTATION GAP |
| 4. VEH0142 EV body-type split | ✅ Yes | ✅ Yes | IMPLEMENTED — MATCHES METHODOLOGY |
| 5. Company-car adjustment | ✅ Yes | ❌ No | IMPLEMENTATION GAP |
| 6. 100-cap redistribution | ✅ Yes | ✅ Yes (partial) | IMPLEMENTED — NEEDS METHODOLOGY VALIDATION (occurs before Step 5) |

**Key gap:** Current implementation performs Steps 1, 4, and 6, omitting Steps 2–3 (total vehicle/van baseline) and Step 5 (company-car adjustment). Step 6 occurs out of sequence (before Step 5).

---

## SECTION 3 — COMMON OUTPUT GRAIN

The dashboard operates at **LSOA21 (2021 Census Lower Layer Super Output Area)** as the standard analytical geography.

### Output Geographies

#### Primary: LSOA21
All LCT and EV data are computed/stored at LSOA21 level. LSOA21CD is the geographic key.

#### Secondary (Derived)
Higher-level outputs are aggregated from LSOA21:

- **MSOA21** (Middle Layer SOA) — within LSOA-to-MSOA lookup
- **LAD22** (Local Authority District 2022) — via LSOA-to-LAD22 bridge
- **LAEP** (Local Area Energy Planning) — via LAD22→LAEP mapping
- **DNO / Licence Area** (EPN, SPN, LPN) — via LSOA→Majority Licence Area lookup
- **National** — sum across all LSOA21

#### Aggregation Rule
Higher-level geography results are always derived by:
```
Aggregate = sum of (retained LSOA21 records) for that geography
```

This ensures consistency: totals can be traced back to underlying LSOA-level detail.

---

## SECTION 4 — METHODOLOGY STATUS & VALIDATION REQUIREMENTS

| Methodology Area | Rule | Evidence/Source | Confidence | Status |
|---|---|---|---|---|
| **LCT Tech Classification** | Pattern match on tech field; unrecognized → exclude | Pipeline 1 code | HIGH | VERIFIED METHODOLOGY |
| **LCT Capacity Unit** | kW (conversions from MW, W) | Pipeline 1 implementation | HIGH | VERIFIED METHODOLOGY |
| **LCT Capacity Field (MCS)** | `Total Installed Capacity` | Pipeline 1 code, MCS schema | HIGH | VERIFIED METHODOLOGY |
| **LCT Capacity Field (LCT Register)** | Technology-specific field selection | Original methodology + Stage 2 analysis | MEDIUM | TO CONFIRM per technology — current Pipeline 1 implementation uses max(Generation_Rating, Import_Rating) |
| **LCT Postcode→LSOA** | postcode_lsoa21_lookup_spatial.csv | Pipeline 1/2 code | HIGH | VERIFIED METHODOLOGY |
| **LCT LSOA→DNO** | LSOA to DNO.csv (Majority Licence area) | Pipeline 1/2 code | HIGH | VERIFIED METHODOLOGY |
| **LCT MCS UKPN Eligibility** | Spatial method (postcode→LSOA→DNO) | Pipeline 1/2 code | HIGH | VERIFIED METHODOLOGY |
| **LCT Register UKPN Eligibility** | Native DNO field (100% UKPN) | Source provenance analysis | HIGH | VERIFIED SOURCE FACT |
| **ECR Large UKPN Eligibility** | Native Licence Area field (100% UKPN) | Source provenance analysis | HIGH | VERIFIED SOURCE FACT |
| **ECR Small UKPN Eligibility** | Native Licence Area field (100% UKPN) | Source provenance analysis | HIGH | VERIFIED SOURCE FACT |
| **ZapMap UKPN Eligibility** | Spatial method (postcode→LSOA→DNO) | Schema inspection, Pipeline 2 code | HIGH | VERIFIED SOURCE FACT |
| **Pipeline 1 Deduplication** | MPAN-only cross-source (MCS primary, LCT Register secondary) | Pipeline 1 code | HIGH | IMPLEMENTED — MATCHES METHODOLOGY |
| **Pipeline 2 Deduplication** | MPAN+Month+Tech within-source (proposed) | Source provenance analysis; not implemented | MEDIUM | TO CONFIRM |
| **Stage 2 Cross-Source Dedup** | MCS vs LCT, MCS/LCT vs ECR, broad vs native | — | LOW | TO CONFIRM |
| **EV VEH0135 baseline** | BEV/PHEV LSOA counts, all body types | Authoritative methodology, code inspection | HIGH | IMPLEMENTED — MATCHES METHODOLOGY |
| **EV VEH0125 total vehicles** | Cars, Motorcycles, Other by LSOA | Authoritative methodology | HIGH | IMPLEMENTATION GAP |
| **EV VEH0105 van proportions** | LA-level "other" → vans | Authoritative methodology | HIGH | IMPLEMENTATION GAP |
| **EV VEH0142 proportions** | BEV/PHEV → cars/vans/HGV/buses/motorcycles/other | Authoritative methodology, code inspection | HIGH | IMPLEMENTED — MATCHES METHODOLOGY |
| **EV Company-Car Adjustment** | LA-level redistribution to match UK company-car ratio | Authoritative methodology | MEDIUM | IMPLEMENTATION GAP |
| **EV 100-Cap Redistribution** | >100 cap, GB-wide pooling, post company-car adjustment | Authoritative methodology | HIGH | IMPLEMENTED (partial) — WRONG SEQUENCE |
| **EV 100-Cap Weighting** | base = min(value, 100); weight = base / sum(base) | Current code | MEDIUM | IMPLEMENTED — NEEDS METHODOLOGY VALIDATION |
| **EV Largest-Remainder Rounding** | Preserve group/LSOA totals exactly | Code inspection | HIGH | IMPLEMENTED — MATCHES METHODOLOGY |
| **Spatial-Native Disagreement (LCT/ECR)** | 652 documented mismatches (0.11%), boundary cases | Source provenance analysis | HIGH | VERIFIED SOURCE FACT |
| **LSOA output granularity** | LSOA21CD aggregation | Pipeline 1/2 code | HIGH | VERIFIED METHODOLOGY |

---

## SECTION 5 — NOTES ON INTERPRETATION

### Authoritative vs. Implemented

This document describes the **intended business methodology**. Where the current implementation differs, the difference is documented as an **IMPLEMENTATION GAP** rather than redefining the methodology to match the code.

### VEH0125 and VEH0105 Status

These are **required by the authoritative methodology** but are currently **not implemented** in the active pipeline. They are not archived or reference files; they are missing inputs.

### Company-Car Adjustment Status

This step is **required by the authoritative methodology** but is currently **not implemented**. No company-car redistribution logic exists in any script.

### 100-Cap Redistribution Framing

The 100-cap redistribution is a **deliberate modelling step** to smooth geographic concentration. It is not primarily a reversal of confidentiality suppression (though VEH0135 may be confidentiality-protected). The exact weighting formula (`base / sum(base)`) has been implemented but is marked as needing methodology validation.

### LCT Methodology

LCT installation methodology (Section 1) is based on code inspection (Pipeline 1) and Pipeline 2 analysis. Where methodology documentation exists (e.g., for specific technologies or regulatory requirements), that documentation should be consulted. Current gaps noted:

- ECR capacity fields (exact column names and units)
- ZapMap capacity interpretation
- Device-Report integration strategy
- Stage 2 cross-source deduplication rules

---

## CONCLUSION

This methodology document establishes the intended business logic for LCT installations and EV vehicle counts. It distinguishes:

1. **Verified methodology** — established through code inspection, source provenance analysis, and authoritative specification
2. **Verified source facts** — confirmed through direct inspection of data
3. **Implemented correctly** — code matches intended methodology
4. **Implemented but needs validation** — code exists but methodology basis is not documented
5. **Implementation gaps** — required steps that are not implemented
6. **To confirm** — details not yet resolved

This structure enables future work to assess whether gaps are acceptable simplifications or true deficits requiring remediation.
