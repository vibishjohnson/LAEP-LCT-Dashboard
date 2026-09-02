# LAEP LCT Dashboard — Development Guide

## ⚠️ CRITICAL SCOPE RULE

**This dashboard is for UK Power Networks only.**

Valid licence areas: **EPN, SPN, LPN only.**

Some source datasets (MCS, LCT Register, external sources) cover the entire UK. **Before comparing totals, validating deduplication, or diagnosing differences between pipelines, first confirm that EVERY dataset has been filtered to UKPN scope.**

For each data source you inspect or modify, explicitly report:
1. Total raw records
2. Records within UKPN scope (EPN, SPN, LPN)
3. Records excluded as non-UKPN
4. The field/lookup used to determine UKPN scope

**Do not compare UK-wide records against UKPN-only records.** All downstream comparisons and deduplication analysis must use UKPN-only records.

---

## Architecture

Two separate LCT processing pipelines exist:

- **Pipeline 1 (Active):** `scripts/04_all_tech_processing.py` → creates `project/output_processed/dashboard_data_*.csv` (consumed by app)
- **Pipeline 2 (New / not yet app-integrated):** `scripts/update_lct_dashboard.py` → creates `output/*.csv`

Pipeline 2 may eventually replace or feed the app, but no migration decision has been made. Do not connect it to the app or treat it as production-equivalent without explicit instruction and validation.

The Streamlit app (`app.py`) currently consumes **Pipeline 1 outputs only**.

Scripts `11_merge_march_forecast_into_actuals.py`, `12_merge_april_forecast_into_actuals.py`, and `13_apply_cumulative_actuals.py` modify Pipeline 1 outputs to inject forecast data for future periods.

## Critical Hardcoded Values

**Date range:** Pipeline 1 filters to Apr 2025–Mar 2026 in `scripts/04_all_tech_processing.py` lines 84 and 157. Changing this range may also require reviewing scripts 11–13 and forecast input files. Do not assume changing script 04 alone is sufficient.

**Technologies:** "Heat Pump", "Solar PV", "Battery Storage", "EV Charger" (string matching in `get_technology_type()` function). Pipeline 2 uses "EV Charging" instead of "EV Charger" — this inconsistency is documented but not standardized.

**DNO/License areas:** EPN, SPN, LPN (queried from LSOA lookup table).

**Capacity unit:** kW in Pipeline 1, MW in Pipeline 2.

## Data Locations

**Inputs (source data):**
- MCS monthly files: `lct/MCS/` (xlsx and csv)
- LCT Register: `lct/LCT Register.csv` (or `lct/lct_register_latest.csv` if using newer Databricks export)
- Lookups: `lookups/postcode_lsoa21_lookup_spatial.csv`, `lookups/LSOA to DNO.csv`
- Forecasts: `project/output_processed/dfes_*.csv` (pre-computed DFES projections)

**Outputs (Pipeline 1, app-facing):**
- `project/output_processed/dashboard_data_dno.csv` (DNO-level aggregation)
- `project/output_processed/dashboard_data_lsoa.csv` (LSOA-level detail)

**Generated (ignored by git):**
- `output/` (Pipeline 2 reports)
- `lct_run.log` (runtime log)
- `lct/lct_register_latest.csv` (Databricks export, externally refreshed)

## Processing Logic

**Pipeline 1 deduplication:** Uses MPAN-only cross-source deduplication. An LCT Register record is removed whenever its MPAN appears in MCS, regardless of month or technology. Pipeline 2 uses MPAN + Month + Technology as the deduplication key. This difference materially affects reported totals: the two approaches differ by approximately 136k installations and 977 MW of capacity. The choice of deduplication strategy must not be changed without explicit validation.

**Technology classification:** Pattern matching in `get_technology_type()`. Unrecognized strings return None and are dropped.

**Capacity parsing:** Numeric extraction with unit conversion (MW→kW, W→kW).

**Geographic allocation:** Postcode → LSOA21CD via postcode lookup; LSOA21CD → DNO via LSOA-to-DNO lookup.

## Validation & Testing

No automated tests. Manual validation only. After running Pipeline 1, verify:
- Record counts do not drop unexpectedly (check printed output)
- Total capacity and installation counts by technology are reasonable
- No months missing (should span Apr 2025–Mar 2026)

Changes to capacity parsing, tech classification, or geographic allocation can materially change reported totals. Treat these as business-logic changes requiring validation.

## Important: Do Not Casually Merge Pipelines

Pipeline 1 and Pipeline 2 have different:
- Output schemas (column names, units, aggregation levels are incompatible)
- Deduplication logic (MPAN-only vs. MPAN+Month+Tech)
- Data sources (Pipeline 2 also ingests ECR, ZapMap, SmartConnect)

Do not attempt to "unify" or "refactor" them as part of another task. Reconnection requires schema translation and careful validation.

## Scripts to Ignore

Do not assume these are part of the production workflow:
- `debug_*.py`, `diagnose_*.py`, `check_*.py`, `test_*.py` (one-off exploratory checks)
- `02_lct_actuals_processing*.py` variants (experimental, superseded)
- Scripts 01, 05–10 (separate workflows for EV and forecast generation)

## Git & Large Files

- **Preserve .gitattributes and Git LFS configuration.** Do not remove or rewrite it.
- **Do not remove existing Git LFS-tracked files** unless explicitly asked.
- **Files in .gitignore must not be committed.**
- **Do not auto-commit new large data extracts.** Confirm first whether they are repository dependencies or externally refreshed source data. `lct/lct_register_latest.csv` is intentionally ignored—it is an external Databricks extract meant to be refreshed manually outside git.

## Making Changes Safely

1. **Understand before modifying:** Read the relevant source script and data assumptions first.
2. **Validation after logic changes:** Run Pipeline 1 (and scripts 11–13 if applicable), then verify totals by technology, DNO, time, and geography. Document material differences.
3. **Do not edit generated CSVs directly:** Fix the processing code instead.
4. **No unrelated cleanup:** Do not refactor or migrate architecture as a side effect of a bug fix or feature.
5. **Test the modified pipeline:** Re-run the affected scripts and confirm the app still loads and displays correctly.

## Change Control Policy

When a session receives an instruction containing words like "analyze," "inspect," "review," "validate," "compare," "investigate," or an explicit statement like "do not modify files," treat the entire task as **READ-ONLY**.

- Do not edit repository files unless the current user instruction explicitly authorizes implementation or modification.
- Do not stage changes (git add) unless explicitly asked to commit.
- Do not create commits unless explicitly asked to commit.
- Do not push to remote unless explicitly asked to push.
- Do not modify CLAUDE.md itself merely because a new rule or fact is discovered during analysis; propose the update first and wait for approval.
- If a change appears important or urgent during a read-only task, report it as a recommendation and wait for explicit authorization before proceeding.

A useful discovery during analysis is not implicit permission to modify the repository. Always confirm scope with the user first.
