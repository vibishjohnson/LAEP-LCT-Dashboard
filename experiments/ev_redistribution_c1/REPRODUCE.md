# Reproducing and verifying Method C1

This covers how to rebuild and check the C1 outputs from version-controlled sources, what each step depends on, and
what must be true of the machine before a release.

## Environment

- **Python 3.10:** pandas, numpy and pytest. Verified with pandas 2.3.3, numpy 2.2.6 and pytest 9.1.1.
- **Node.js 18** (verified with 18.20.8). Needed only for the dashboard regression harness, which is run as `node --no-opt --no-sparkplug`.
- **Git with Git LFS.** Every `*.csv` is an LFS object (root `.gitattributes`). This folder's `.gitattributes` keeps the hash-pinned JSON files byte-exact.
- **Windows and path lengths:** keep checkout and temporary paths short. The release tool refuses roots whose planned paths would exceed 250 characters.
- **Bytecode:** always set `PYTHONDONTWRITEBYTECODE=1`, because some `__pycache__` folders are tracked in this repository.

## Inputs (all read-only)

| Input | Status | Used by |
|---|---|---|
| `ev/df_VEH0135.csv` (DfT VEH0135, 2026 Q1 vintage) | Tracked (LFS) | `run_c1.py` |
| `lookups/LSOA to DNO.csv`, `lookups/LA-LAEP lookup.csv` | Tracked (LFS) | `run_c1.py`, `integration_writer.py`, release preflight |
| `project/output_processed/integration/geography_lookup.csv` | Tracked (LFS) | LAEP mapping |
| `project/output_processed/ev_quarterly_lsoa.csv` (Method A) | Tracked (LFS) | Method A reference in `run_c1.py`; archive provenance |
| `project/output_processed/integration/ev_actuals_*.csv` (Method A, live) | Tracked (LFS) | Release targets; the archive is built from the DNO file |
| `dashboard.html`, `integration/monthly_forecast_*.csv`, HP files, `dashboard_comparison_*.csv` | Tracked | Dashboard regression and release protection |
| `integration/metadata.json` | **Tracked, but the live working copy differs from HEAD** (it adds `"ev_vintage": null`, written by `promotion.py` on 2026-10-01; sha256 `79fa5212…`) | Pinned by the release preflight (see Preconditions) |
| Operational runner `dashboard_integration` | **External**. Location from `C1_RUNNER_DIR`, or else `integration_runner/local_paths.json` (git-ignored). Source copy committed in `integration_runner/`. | Runner-guard checks; the reversion scan R1 |
| `project/dft_staging/…`, `project/production_backups/…`, `project/promotion_history.jsonl` | **Local admin state** (untracked, machine-specific) | Only some tests use it as fixtures; they skip with an explicit reason when it is absent |

## Committed release artefacts (immutable, hash-pinned in `release/release_config.py`)

- **`outputs/c1_lsoa_quarterly.csv`:** the C1 LSOA output. `run_c1.py` reproduces it byte-for-byte.
- **`outputs/run_metadata.json`:** run provenance. It contains the run timestamp, so a re-run produces different bytes. **The committed file is the authoritative record.**
- **`shadow_integration/*`:** the three dashboard files to publish, plus their manifests. `integration_writer.py` reproduces the CSVs byte-for-byte from the C1 LSOA output.
- **`method_a_archive/*`:** the frozen Method A history, with its source also preserved in git history (`git show 71610eb:project/output_processed/integration/ev_actuals_dno.csv`). The writer never rewrites an existing archive. It verifies the archive and refuses to build one once production no longer holds Method A.

Other files in `outputs/` (comparisons, QA report, hotspots and so on) are reproducible analysis outputs and are not committed.

## Order of execution

**Run 1–2 only in a disposable copy, or in an approved quarterly update.** They write to `outputs/`, `shadow_integration/` and `method_a_archive/`, which hold the pinned release artefacts.

1. `python experiments/ev_redistribution_c1/run_c1.py`: builds the C1 LSOA output and runs 357 QA checks into `outputs/`.
2. `python experiments/ev_redistribution_c1/integration_writer.py`: builds the shadow files. It writes nothing when their content is unchanged; the archive is write-once.
3. `python -m pytest experiments/ev_redistribution_c1/tests -p no:cacheprovider -q --rootdir=experiments/ev_redistribution_c1`
4. `python experiments/ev_redistribution_c1/release/c1_release.py preflight --root <repository root>` (read-only).
5. `python experiments/ev_redistribution_c1/protections/reversion_scan.py` (read-only; it must report 0 blockers).

The release, rollback and recovery commands are in `release/RELEASE_CHECKLIST.md`. Re-pinning after a legitimate forecast refresh is done with `release/repin_metadata.py`.

## Preconditions for a release on a given machine

- **Live state matches the pins:** the live `integration/` files equal their pinned hashes. On a fresh checkout, `metadata.json` is the HEAD version (without `ev_vintage`), so the preflight fails closed on that single pin. Resolve it either by restoring the reviewed live file (sha256 `79fa5212…`) or with an approved `release/repin_metadata.py` entry. Never edit pins by hand.
- **Guarded runner:** the operational runner must be the guarded version. `integration_runner/tests` and `tests/test_runner_guard.py` verify that it matches `SOURCE_MANIFEST.json`.
- **Release switch off:** `LIVE_RELEASE_ENABLED` stays `False` until a separately approved change.

## Verifying an installed C1 release

- `c1_release.py verify-c1 --root <repository root>` must exit 0. All three targets must hold the C1 bytes: first period 2025-03, five quarters, UKPN March 2026 = 896,098.
- The dashboard regression suites must pass against production with no redirect.
- The reversion scan must report 0 blockers.
