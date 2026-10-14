# Preventing Method A from coming back after a C1 release

Investigated on 2026-10-12 by reading files only. No runner, script or scheduled task was changed or executed.

## 1. What can write Method A into `integration/ev_actuals_*.csv`

| # | Producer | How it gets to production | Evidence | Status |
|---|---|---|---|---|
| R1 | **External integration runner**: `<runner dir>\run_integration.py` (location from `C1_RUNNER_DIR` or `integration_runner/local_paths.json`), function `run()`, `__main__` entry point, using `actuals.py` and `config.py` | `actuals.load_ev_actuals()` reads `project/output_processed/ev_quarterly_lsoa.csv` (Method A, from script 05) from 2025-03 onwards. On QA PASS it runs `shutil.copy2` for **9 files** into `project/output_processed/integration/`: the three `ev_actuals_*.csv`, all three `monthly_forecast_*.csv`, `geography_lookup.csv`, `metadata.json` and `run_report.md`. | Production `ev_actuals_la.csv` and `ev_actuals_laep.csv` are byte-identical to the runner's `outputs/` (sha256 `f4c3ef85…`, `b9d0a351…`). `config.DASHBOARD_INTEGRATION_PUBLISH` points at this repository. `hp_metadata.json` names the runner folder. | **Release blocker.** Its next run would overwrite C1, and also the restored `monthly_forecast_dno.csv` and `metadata.json`. |
| R2 | **Manual script chain** | `scripts/05_ev_quarterly_processing.py` writes `ev_quarterly_lsoa.csv`. `scripts/09_prepare_ev_actuals_dno*.py` and scripts 12/13 then write `project/output_processed/ev_actuals_dno.csv`, which was **copied by hand** into `integration/`. | Commit `71f70e2` (2026-10-01): "Copied corrected file to integration/ev_actuals_dno.csv". The live DNO file (58 quarters from 2011-12, `4878bba6…`) is identical to `project/output_processed/ev_actuals_dno.csv`. | **Release blocker** (process/documentation). |
| R3 | **`data_manager/promotion.py`**, run from the Flask admin app `app_data_manager.py` | `EV_CHANGED` lists `integration/ev_actuals_{dno,la,laep}.csv` and `monthly_forecast_*`. Promoting a DfT/SMMT candidate from `project/dft_staging/` replaces them. `restore_backup()` deletes and copies back the whole `output_processed/` tree from `project/production_backups/`, all of which hold Method A EV files. | Backups hold `a5390820…` (runner output) and `9faec76a…` (SMMT candidate). `project/promotion_history.jsonl` shows promotions and rollbacks on 2026-10-01. | **Release blocker.** |
| R4 | **Candidate builders**: `scripts/generate_complete_production_candidate*.py`, `data_manager/smmt_candidate_builder.py` | They build Method A `ev_actuals_*` into candidate folders, which then feed R3. | Matches found by searching the repository. | Covered by fixing R3. |
| R5 | **Git** | HEAD stores Method A in LFS (oids `4878bba6…`, `f4c3ef85…`, `b9d0a351…`). A `git checkout`/`restore` of these paths, or a merge without the C1 commit, brings Method A back. | `git cat-file -p HEAD:<path>` | Mitigated by committing C1 after release (post-release step). |
| R6 | `tests/test_phase5_end_to_end.py` | Exercises `promotion.py` with the EV paths, and writes into `project/production_backups/`. | Source | Do not run against production. |

Who invokes R1: no scheduled task matches (checked with `Get-ScheduledTask`). No launcher refers to it: `run_disaggregation.bat` in the same project runs only the NESO disaggregation scripts. It is run **by hand by the analyst**; the last run was 2026-09-25 15:32–15:35 according to `run_report.md`. The HP runner `hp_run_integration.py` publishes only `hp_*` files and has no EV-actuals path.

## 2. Identifying the producers reliably before release

1. Run `python experiments/ev_redistribution_c1/release/c1_release.py status --root "<repo>"`. It is read-only and must show `METHOD_A` for all three targets before release.
2. Record the sha256 of the external runner's `run_integration.py`, `actuals.py` and `config.py`, and confirm that `DASHBOARD_INTEGRATION_PUBLISH` still lists the `ev_actuals_*` files.
3. Re-check that no scheduled task invokes it: `Get-ScheduledTask | ? { $_.Actions.Arguments -match 'integration' }`.
4. Search the repository for new writers: `rg -l "ev_actuals_(dno|la|laep)" --glob "*.py"`. The known list is R1–R6.

## 3. Disabling the producers (a later, separately authorised phase; nothing is changed now)

- **R1:** remove the three `ev_actuals_*` names from the runner's publish list, so it keeps publishing forecasts only after review. Alternatively, make it publish EV actuals by copying the C1 shadow files and verifying their hashes. Add a guard that refuses to publish when `verify-c1` would fail afterwards. Ideally, also stop it overwriting `monthly_forecast_*` and `metadata.json` without review.
- **R2:** mark scripts 09/12/13 as legacy for the dashboard. Their output in `project/output_processed/ev_actuals_dno.csv` must never be copied into `integration/` again. Record this in the runbook and, once approved, in CLAUDE.md.
- **R3:** take `integration/ev_actuals_*` out of `EV_CHANGED`, or require a C1 release manifest. Replace the whole-directory restore with file-level restores.
- **R5:** commit the C1 targets in a separate approved commit right after release, so that git holds C1.

## 4. One producer and the quarterly workflow

Only one path may produce EV actuals:

`run_c1.py` → `integration_writer.py` (shadow) → tests → `c1_release.py preflight` → approvals → `c1_release.py promote`.

Each quarter:

1. Refresh `ev/df_VEH0135.csv` outside git.
2. Extend the periods in `run_c1.py` and `integration_writer.py`, re-run both, then run the whole test suite.
3. Re-pin `release_config.py` (shadow hashes, expected totals, Method A or previous-C1 target hashes) in a reviewed change.
4. Run preflight, get approvals, promote, run `verify-c1`, then commit.

This is an operator workflow, deliberately not automatic.

## 5. Automated detection

`c1_release.py verify-c1 --root <repo>` is read-only and exits with code 6 on failure. It fails when:
- any target is not the validated C1 bytes;
- any file's first period is not 2025-03, or it has a number of periods other than five (Method A history or a mixed series);
- the DNO March 2026 total equals the Method A value of 908,964.

The tests show it reporting `METHOD_A` on the current production files today. Run it after every data job, before every commit that touches `integration/`, and in any later CI or pre-commit hook (a later phase).

> **Phase 6 update:** the pathways were re-verified against the code, and proposed patches, tests, a read-only scanner and a pre-commit guard prototype were added in `../protections/`; see `protections/REMEDIATION_PLAN.md`.
>
> Two corrections to this document:
> - R1 also produces the DFES/NESO forecasts, so it must be patched, not retired.
> - R3 additionally has a live promotable stale candidate (R3a).
