# EV redistribution candidate — Method C1 (`C1_DYNAMIC_PRIVATE_EV`)

**Experimental candidate only.** Not used by `dashboard.html`, the integration files or any production pipeline.
Production EV actuals remain Method A (`scripts/05_ev_quarterly_processing.py`).

Outputs are **modelled** estimates: DVLA keeper-address registrations after a modelled redistribution.
They are not observed vehicle locations.

## Run

From the repository root:

```bash
python experiments/ev_redistribution_c1/run_c1.py                                   # writes ./outputs only
python -m pytest experiments/ev_redistribution_c1/tests -p no:cacheprovider -q       # unit + full-data tests
```

Set `PYTHONDONTWRITEBYTECODE=1` to avoid creating `__pycache__` folders.

All writes go through `run_c1.guarded_path`, which refuses any path outside `experiments/ev_redistribution_c1/outputs/`.

## Inputs (read-only)

- `ev/df_VEH0135.csv` — DfT ULEV stock by LSOA, fuel and keepership (Total, Private, Company rows).
- `lookups/LSOA to DNO.csv` — canonical UKPN LSOAs (11,023) and majority licence area.
- `project/output_processed/integration/geography_lookup.csv` — LAD and LAEP mapping (as used by the dashboard).
- `project/output_processed/ev_quarterly_lsoa.csv` — production Method A output, used only for regression comparison.

## Method

Fuels: BEV = "Battery electric"; PHEV = "Plug-in hybrid electric (petrol)" and "(diesel)". Same as production.
Quarters: 2025 Q1 to 2026 Q1 (March 2025 to March 2026), each processed independently.
Universe: every VEH0135 area with BEV+PHEV > 0 in the quarter (GB, Northern Ireland and the non-geographic `Miscellaneous` row), as in production.

Per area and fuel (suppressed `[c]`/`[x]` cells read as 0):

| Component | Definition |
|---|---|
| Private P | reported Private, capped at Total |
| Company C | reported Company, capped at Total − P |
| Unclassified U | Total − P − C (keeper cell suppressed or incomplete) |

Retention, using combined BEV+PHEV:

- All private vehicles are kept in their LSOA. A private vehicle is never pooled.
- Company and unclassified vehicles form one non-private block. It keeps `min(C + U, max(0, 100 − P))`.
  - The kept amount is split between BEV and PHEV in proportion to the block's fuel mix.
  - Both shares are rounded down, and any remaining vehicle goes to the larger remainder (ties to BEV).
  - Everything else in the block is pooled nationally.
- Unclassified vehicles are never treated as private. They share the allowance with company vehicles in proportion, with no priority.
  - This matches the prior four-scenario and C1 experiments.
  - The `retained_company_diag` and `retained_unclassified_diag` columns attribute the kept block, flooring the company share. This attribution is diagnostic only and does not affect results.
- `Miscellaneous` (non-geographic) keeps `min(Total, 100)` exactly as production, pools the rest, and never receives.

Redistribution:

- **Recipients:** every geographic area in the universe, including areas above 100. Northern Ireland is included, as in production.
- **Weight:** the area's reported private BEV+PHEV in the same quarter divided by the national total. Weights are recalculated every quarter, and areas with no reported private EVs get zero weight.
- **Integer rounding:** BEV and PHEV pools are distributed separately using the same weights. Values are floored, then +1 is added by largest remainder, with ties broken by ascending LSOA code.

London adjustment: **C1 applies a 20% minimum LPN share** (`c1_method.C1_LPN_MIN_SHARE = 0.20`), recalculated each quarter on C1's own post-redistribution totals:

- **Uplift:** `x = max(0, round((0.20 × UKPN_post − LPN_post) / 0.80))`.
  - It is only applied when positive.
  - LPN stock is never reduced when its share already exceeds 20%.
- **BEV/PHEV split:** BEV share = `int(x × LPN BEV fraction)`, PHEV = the remainder.
- **Distribution:** each fuel is spread across LPN LSOAs in proportion to their post-redistribution stock, using largest remainder (production rounding).
- **Result:** in all five quarters (Mar-25 to Mar-26), LPN's post-redistribution share is 23.07% → 21.36%. That is above 20%, so the floor never activates: the uplift is 0 and final = post-redistribution.
- **Method A reference:** keeps production's 25% target (`PRODUCTION_LPN_TARGET_SHARE`) so it still reproduces the production file exactly.

Output covers all 11,023 canonical UKPN LSOAs. LSOAs with no EVs in a quarter appear as explicit zero rows.

## Outputs (`outputs/`)

| File | Content |
|---|---|
| `c1_lsoa_quarterly.csv` | C1 by canonical LSOA × quarter × fuel, with keeper components, retained/pooled/received, uplift, final and provenance |
| `c1_geography_aggregates.csv` | C1 aggregated to UKPN, DNO, 121 LAs and 14 LAEPs (BEV, PHEV, Total) |
| `comparison_A_vs_C1.csv` | final stock, A vs C1, every geography and quarter |
| `national_accounting.csv` | national pools, keeper components, Miscellaneous balance, recipients, uplift |
| `volatility_A_vs_C1.csv` | quarter-on-quarter LSOA changes by subtype |
| `c1_weight_and_pool_movement.csv` | pool growth and weight-driven movement |
| `high_impact_geographies.csv`, `hotspot_lsoas.csv` | selected areas and fleet hotspots |
| `method_A_production_reproduction.csv` | Method A reference vs production LSOA file |
| `qa_checks.csv`, `qa_report.md`, `run_metadata.json` | QA results and provenance |

`Method A` rows are a reference reimplementation used only to prove the shared primitives reproduce production exactly.

## Known limitations

- Recipient weighting assumes pooled fleet vehicles are located like private EVs. No independent location data has validated this.
- The weighting is circular: it concentrates vehicles where private EVs already are.
- About 100k–122k EVs per quarter are in suppressed keeper cells (unclassified). Most of them are zero-weight areas in C1.
- C1 departs from Ofgem RIIO-ED2 Annex B wording, which pools vehicles from areas above 100 to areas below 100.
- The 20% LPN floor is an unvalidated modelling assumption. It is currently inactive, but it would activate if LPN's post-redistribution share fell below 20%.

## Integration writer and dashboard regression (Phase 4, shadow only)

| Path | Purpose |
|---|---|
| `integration_writer.py` | Aggregates `outputs/c1_lsoa_quarterly.csv` into production-schema `ev_actuals_{dno,la,laep}.csv` (2025-03..2026-03). Writes only to `shadow_integration/`. Also writes `method_a_archive/` (the current production DNO file plus its pre-2025-03 Method A history). The write guard allows only those two directories. |
| `shadow_integration/` | Shadow files, `manifest.json` (input and output hashes) and `metadata_patch_proposal.json`. Not production. |
| `dashboard_regression/dashboard_harness.js` | Headless, read-only run of the root `dashboard.html`, with optional redirection of the EV integration fetches to the shadow files. |
| `tests/test_integration_writer.py` | Reconciliation, schema, determinism, archive, write-guard and invalid-input tests. |
| `tests/test_dashboard_regression.py` | EV chart, KPI, forecast-invariance, switching, cross-technology isolation and damaged-file tests (needs `node`). |
| `PROMOTION_RUNBOOK.md` | Promotion plan. Not executed. |

```
set PYTHONDONTWRITEBYTECODE=1
python experiments/ev_redistribution_c1/integration_writer.py
python -m pytest experiments/ev_redistribution_c1/tests -p no:cacheprovider -q --rootdir=experiments/ev_redistribution_c1
```

## Reproduction, release and safeguards

- `REPRODUCE.md`: environment, inputs, execution order, release preconditions and post-release verification.
- `release/`: guarded release tool and checklist (`LIVE_RELEASE_ENABLED = False`).
- `protections/`: read-only reversion scan, quarantine tool and pre-commit guard prototype.
- `local_paths.py`: resolves the external runner location (`C1_RUNNER_DIR`, or else `integration_runner/local_paths.json` (git-ignored)).
