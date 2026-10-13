# Dashboard integration runner (version-controlled copy)

This folder is the auditable source for the external "dashboard_integration" runner. That runner builds the
DFES/NESO monthly forecast integration files for the dashboard. Hashes for every file are in `SOURCE_MANIFEST.json`.

| | |
|---|---|
| Operational location | A separate local folder `<EV project>\\dashboard_integration\\` (machine-specific; configured in `local_paths.json`, see below) |
| Before 2026-10-13 | Untracked in a separate local git repository; no history |
| Original `run_integration.py` | sha256 `d39b8765c9dc554477cec230691a5ee42867479af6b4d9c998d1dcbdd86a2368`, kept in `original/run_integration.py` and beside the operational copy as `run_integration.py.pre_c1_guard_20261013.bak` |
| Current files | `dashboard_integration/` here is identical to the operational folder (line endings aside), except that `config.py`, `hp_config.py` and `hp_qa.py` resolve machine-specific paths from local configuration. `SOURCE_MANIFEST.json` records both hashes and the replaced lines; `provenance.py` and `tests/test_runner_guard.py::test_operational_runner_matches_version_controlled_copy` check this. |
| Git status | **Not yet committed.** Committing this folder needs a separate approval. |

## Invocation (unchanged)

Run by hand; there is no scheduled task or launcher:

```
cd "<EV project folder>"          # the folder that contains dashboard_integration\\
python -m dashboard_integration.run_integration
```

It must be run as a module, because the package uses relative imports. The HP runner is
`python -m dashboard_integration.hp_run_integration`; it publishes only its `hp_*` files and is unchanged.

## Local configuration (not version-controlled)

Machine-specific locations are not stored in the repository. Set environment variables, or copy
`local_paths.example.json` to `local_paths.json` (git-ignored) in the folder that contains `dashboard_integration\`:

| Setting | Environment variable | `local_paths.json` key |
|---|---|---|
| Dashboard repository root (`<dashboard>`) | `LCT_DASHBOARD_ROOT` | `dashboard_root` |
| Heat pump project folder (HP runner only) | `LCT_HP_PROJECT` | `hp_project` |
| Operational runner folder (provenance checks only) | `C1_RUNNER_DIR` | `operational_runner_dir` |

If a location is not configured, imports still work but any run fails on its first input read, naming the missing setting.
Nothing is published: publication happens only after QA has read and passed every input.

## Inputs (read-only; see `config.py`)

- NESO HT/HTB LSOA forecasts: `..\ht_ukpn_final_outputs\` (Lct_BB001/BB002)
- DFES 2026 workbooks: `<dashboard>\dfes\DFES 2026\*.xlsx`
- Lookups: `<dashboard>\lookups\LSOA to DNO.csv`, `LA-LAEP lookup.csv`, the ONS LSOA→LAD lookup
- `<dashboard>\project\output_processed\ev_quarterly_lsoa.csv`. This is Method A, used only for the runner's own EV actuals QA output, which is **no longer published**.
- `<dashboard>\project\output_processed\dfes_ev_forecast_all.csv` (an old-versus-new comparison)

## Outputs

- **Runner's own folder** (`dashboard_integration\outputs\`, `integration_qa\`): annual anchors; `monthly_forecast_{dno,la,laep}.csv`; `geography_lookup.csv`; `ev_actuals_*.csv` (Method A, QA only); `metadata.json`; `run_report.md`.
- **Published to `<dashboard>\project\output_processed\integration\`** only through `publish_guard.publish()`, and only after QA PASS:
  - `monthly_forecast_dno.csv`, `monthly_forecast_la.csv`, `monthly_forecast_laep.csv`;
  - `geography_lookup.csv`, `metadata.json`, `run_report.md`.
- **Never published:** `ev_actuals_dno.csv`, `ev_actuals_la.csv`, `ev_actuals_laep.csv`. Dashboard EV actuals come only from the C1 release tool (`experiments/ev_redistribution_c1/release/c1_release.py`).

## Forecast contract enforced before anything is written

Columns are `<geo>, value, time_grain, tech_type, EV_Type, forecast_source, forecast_scenario, …, observation_source, period, …`, where `<geo>` is `DNO`, `LAD22CD` or `LAEP`.

- **Rows:** only DFES/NESO forecasts (no actuals or other sources). All six series must be present: DFES HolisticTransition and NESO HolisticTransition/HolisticTransitionBasic, each for BEV and PHEV.
- **Keys:** no duplicate (period, geography, source, scenario, fuel) keys.
- **Coverage:** monthly with no gaps, and every series × geography complete. Today that is 2025-03 to 2051-03 (313 months) for 3 DNOs, 121 LAs and 14 LAEPs.
- **Values:** non-negative and numeric.
- **No shrinkage:** neither the period range nor the geography set may shrink versus the published file.
- **Nowcast:** existing published `SMMT_NOWCAST` rows are never dropped silently (override with `C1_ALLOW_DROP_NOWCAST=1`).
- **Geography:** `geography_lookup.csv` must not change unless C1 has been re-validated (override with `C1_ALLOW_GEOGRAPHY_CHANGE=1`).
- **Metadata:** `metadata.json` `latest_actual_period` must equal the published EV actuals' latest period.

Each replaced file is first backed up, with hashes, under `dashboard_integration\publish_backups\publish_<timestamp>\`. It is then replaced through a verified temporary copy and `os.replace`. Published EV actuals are hashed before and after, and must be unchanged.

## Rolling back the runner change

Copy `run_integration.py.pre_c1_guard_20261013.bak` over `run_integration.py` in the operational folder, and delete `publish_guard.py`. Doing so **reinstates EV actuals publication** (Method A), so roll back only with approval.

## Tests and known limitations

`python -m pytest integration_runner/tests -p no:cacheprovider -q` uses synthetic data in temporary directories only. It checks that `SOURCE_MANIFEST.json` matches these files and, when present, the operational folder.

**Known limitation (fails closed):** the period-range shrinkage check also counts already published `SMMT_NOWCAST` rows. Nowcast months beyond the forecast horizon would therefore cause a refusal. Today the nowcast months (Apr–Aug 2026) lie inside the 2025-03..2051-03 horizon, so this cannot trigger.
