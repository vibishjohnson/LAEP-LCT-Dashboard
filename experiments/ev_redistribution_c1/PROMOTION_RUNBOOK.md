# C1 EV actuals — promotion runbook (PLAN ONLY)

Status: **not executed.** Promotion needs explicit written approval. Nothing in this directory writes to production.

> **Superseded in Phase 5:**
> - §4 and §5 are replaced by the guarded release tool `release/c1_release.py`, `release/RELEASE_CHECKLIST.md` and `release/REVERSION_PREVENTION.md`.
> - The Phase 5 allowlist is exactly the three `ev_actuals_*` files, so the release does **not** patch `metadata.json` (step 4.4 is withdrawn). Its `latest_actual_period` is already 2026-03, and C1 provenance is kept in the release manifest instead.

## 1. What would change

| Item | Value |
|---|---|
| Methodology | `C1_DYNAMIC_PRIVATE_EV` (see `README.md`) |
| LPN floor | 20% (`lpn_min_share = 0.2`; never active for 2025-03..2026-03) |
| Periods | 2025-03, 2025-06, 2025-09, 2025-12, 2026-03 (five quarters only) |
| Scope | UKPN only: 11,023 LSOAs (EPN 4,993 / LPN 2,931 / SPN 3,099), 121 LAs, 14 LAEPs |
| Source | `outputs/c1_lsoa_quarterly.csv` (sha256 in `shadow_integration/manifest.json`) |

**Replacement allowlist — exactly three files, nothing else:**

| Shadow file | Production target | Rows | sha256 |
|---|---|---|---|
| `shadow_integration/ev_actuals_dno.csv` | `project/output_processed/integration/ev_actuals_dno.csv` | 30 | `b558a6b0…` |
| `shadow_integration/ev_actuals_la.csv` | `project/output_processed/integration/ev_actuals_la.csv` | 1,210 | `b8c737ea…` |
| `shadow_integration/ev_actuals_laep.csv` | `project/output_processed/integration/ev_actuals_laep.csv` | 140 | `ec539024…` |

Full hashes are in `shadow_integration/manifest.json`. Schemas, column order, dtypes, CRLF endings and sort order match production. The writer reproduces the current production files byte for byte when fed the Method A LSOA output (`tests/test_integration_writer.py::test_writer_reproduces_production_files_byte_for_byte_from_method_a`).

**Must not change at promotion:**
- all `monthly_forecast_*` and `dfes_*` forecast files, including the restored `integration/monthly_forecast_dno.csv` (sha256 `6ce3d9a4…1fd909`);
- every `hp_*` integration file;
- solar and charger outputs;
- `geography_lookup.csv`;
- the nowcast inputs (`lsoa_ev_bodytype.csv`, SMMT/DfT);
- `ev_quarterly_lsoa.csv`;
- `dashboard.html`.

**Expected headline values (stock, UKPN):**

| Quarter | C1 (new) | Method A (current) |
|---|---|---|
| 2025-03 | 719,684 | see `method_a_archive/` |
| 2026-03 | 896,098 | 908,964 |

At Network level the chart opening point stays at DFES March 2025 (661,867 for UKPN), so Network charts change only from June 2025 onwards. LA and LAEP charts open on the C1 March 2025 value.

## 2. Prerequisites (separate, approved changes made before promotion)

1. Commit the Phase 1 restored data files on their own:
   - `integration/monthly_forecast_dno.csv` (from `aa39e40`);
   - the LA/LAEP files restored from HEAD.

   Today they exist only in the working tree.
2. Commit the Phase 2 working-tree `dashboard.html` (sha256 `51fcb61f…`) on its own. The regression tests were written against that version. Re-run them if it changes.
3. Settle who owns the integration writer. The current production `ev_actuals_*` files came from an external "dashboard_integration" runner, not from any script in this repository. Retire that runner, or point it at C1, before promotion. Otherwise it will overwrite C1 with Method A.
4. Guard script 05:
   - `scripts/05_ev_quarterly_processing.py` still writes Method A `ev_quarterly_lsoa.csv`.
   - It does not touch `integration/`, but any external aggregation of its output would regenerate Method A integration files.
   - Do not run that aggregation after promotion.
5. **Do not use `data_manager/promotion.py` for this change:**
   - its `EV_CHANGED` list includes `monthly_forecast_*`, so it would overwrite the restored forecast;
   - `restore_backup` deletes the whole `output_processed` tree and copies it back.

## 3. Pre-promotion QA gates (all must pass on the promotion machine)

```
set PYTHONDONTWRITEBYTECODE=1
python experiments/ev_redistribution_c1/integration_writer.py
python -m pytest experiments/ev_redistribution_c1/tests -p no:cacheprovider -q --rootdir=experiments/ev_redistribution_c1
```

1. All tests pass:
   - `test_c1_unit.py`, `test_c1_full_data.py`
   - `test_integration_writer.py`: 1 symlink test skips on Windows without the symlink privilege.
   - `test_dashboard_regression.py`: needs `node`.
2. Re-running the writer leaves the shadow and archive outputs byte-identical (the test checks that `shadow_integration/` is not stale).
3. The input hashes in `shadow_integration/manifest.json` match the files on disk. Re-run C1 first if `ev/df_VEH0135.csv` or the lookups changed.
4. Record a sha256 manifest of every file under `project/output_processed/` (the "pre" manifest).

## 4. Promotion steps (file-level, three files only)

1. Back up the three current `ev_actuals_*` files and `metadata.json` to a dated folder **outside** `project/output_processed/`, for example `backups/ev_c1_promotion_<date>/`. Record their hashes.
2. Confirm that `method_a_archive/` holds:
   - `ev_actuals_dno_method_a_original.csv`, which must hash-match the current production DNO file;
   - `method_a_dno_history_before_2025-03.csv` (318 rows, 2011-12 to 2024-12, `A_PRODUCTION_LEGACY`).

   Method A history is kept only there and is **not** comparable with C1.
3. Copy the three shadow files over the three production targets. Use byte copy; don't re-serialise.
4. Apply `shadow_integration/metadata_patch_proposal.json` to `integration/metadata.json`:
   - add `ev_actuals_methodology_id`, `ev_actuals_lpn_min_share`, `ev_actuals_first_period` and `ev_actuals_method_a_archive`;
   - leave `latest_actual_period` at `2026-03`, which already matches the C1 data;
   - change no other key.
5. Post-promotion manifest: compared with the pre manifest, **exactly** the three `ev_actuals_*` files and `metadata.json` differ. Any other difference triggers rollback.
6. Run the dashboard regression suite against production, with no redirect:
   - EV values must now equal the shadow expectations;
   - forecasts, titles, caveats, axes and non-EV views must be unchanged.
7. Manually check in a browser (not covered by the headless harness):
   - EV charts for UKPN, EPN, Peterborough LA, Westminster LA, and the Norwich and Outer East LAEPs;
   - the Heat Pump LA view still loads.

## 5. Rollback

Copy the four backed-up files from step 4.1 back over their targets, then verify their hashes against the backup manifest. Never restore by replacing the whole directory.

## 6. KPI labels

- **"Earliest Observation"** reads the first actual period in the data. With the five-quarter files it shows `(2025-03)`. With Method A history it would show `(2011-12)` at Network level. That is why the history is archived, not kept in the live file.
- **"Latest Actual Stock"** labels its period from the static `metadata.json` key `latest_actual_period`, not from the data. Today both are `2026-03`.
  - **Interim rule:** whenever new quarters are added, update `latest_actual_period` in the same change as the data. The QA gate is `max(period)` of `ev_actuals_dno.csv` == `latest_actual_period`; `test_metadata_patch_is_proposal_only_and_consistent` enforces this for the shadow files.
  - **Proposed later dashboard change** (needs separate approval; not part of this promotion): derive the label from `max(period)` of the loaded EV actuals, and fall back to metadata only when no actuals are loaded.

## 7. Resilience risks found by the regression harness (unfixed)

1. **A bad DNO file takes down the whole dashboard.** A malformed or missing `ev_actuals_dno.csv` stops startup with a visible error, for every technology.
2. **A malformed `ev_actuals_la.csv` or `ev_actuals_laep.csv` fails silently.**
   - It throws inside the level-button handler, which has no `catch`, so no message is shown.
   - The EV LA/LAEP charts are blank.
   - **Heat Pump LA/LAEP charts are blank too**, because the HP load after it is skipped.
3. **A missing LA/LAEP file** silently gives blank EV LA/LAEP charts.

The byte-copy and hash gates in §4 are the mitigation. A dashboard hardening change (try/catch around the lazy loads, a visible error) is recommended separately.

## 8. Quarterly update workflow (after promotion)

1. Refresh `ev/df_VEH0135.csv` outside git, as today.
2. Re-run C1: `run_c1.py`, with its periods extended.
   - Validate national BEV/PHEV conservation and the LPN floor (20%, add-only).
   - Review the A-vs-C1 comparison.
3. Extend `EXPECTED_PERIODS` in `integration_writer.py`, re-run the writer, and run all tests.
4. Promote with §3–§5, and update `latest_actual_period` in the same change.
5. The nowcast stays on its own pipeline and baseline. Do not rebase it to C1 without a separate decision.
