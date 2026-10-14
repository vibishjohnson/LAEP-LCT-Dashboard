# C1 EV actuals — final release checklist (NOT EXECUTED)

Release: Method **C1_DYNAMIC_PRIVATE_EV**, 20% LPN floor (inactive), five quarters 2025-03 to 2026-03.

Tool: `experiments/ev_redistribution_c1/release/c1_release.py`.
Pinned expectations: `release_config.py`.

Every box must be ticked, with evidence attached, before the live gate is opened.

## A. Independent approvals (named people, recorded in the release ticket)

- [ ] Methodology owner: C1 decisions are final (private protected; joint company/unclassified allowance; national pool; all geographic recipients; same-quarter private weights; BEV/PHEV conservation; 20% floor).
- [ ] Data owner: the five-quarter series replaces Method A in the dashboard; Method A history stays only in `method_a_archive/`.
- [ ] Dashboard owner: the KPI and opening conventions (Network opens on DFES; LA/LAEP open on C1 March 2025).
- [ ] Release approver: approves this release id and the preflight digest, and the separate change that sets `LIVE_RELEASE_ENABLED = True`.

## B. Prerequisites (separate, approved commits; none made in Phase 5)

- [ ] Commit the restored `integration/monthly_forecast_dno.csv` (sha256 `6ce3d9a4…1fd909`) and the restored LA/LAEP forecast files on their own.
- [ ] Commit the working-tree `dashboard.html` (sha256 `51fcb61f…860a4fc8`) on its own. Re-run the dashboard regression suites if it changes.
- [ ] Resolve the reversion blockers in `REVERSION_PREVENTION.md`:
  - R1: the external runner no longer publishes `ev_actuals_*`;
  - R2: the scripts 09/12/13 copy path is retired;
  - R3: `promotion.py` no longer replaces `ev_actuals_*` or restores whole directories.
- [ ] If any pinned file changed through these commits, re-pin `release_config.py` in a reviewed change and re-run every test.

## C. Pre-release verification (on the release machine)

- [ ] `git status`, HEAD and staged changes recorded. Nothing unexpected is staged.
- [ ] Run the full suite. It must pass, with only the documented skip (file symlinks without privilege):
  ```
  set PYTHONDONTWRITEBYTECODE=1
  python -m pytest experiments/ev_redistribution_c1/tests -p no:cacheprovider -q --rootdir=experiments/ev_redistribution_c1 -rs
  ```
- [ ] Re-running `integration_writer.py` leaves the shadow and archive outputs byte-identical (`test_rerun_is_byte_identical_and_matches_committed_shadow`).
- [ ] Rehearse the dry run in a fresh disposable environment (section F).
- [ ] Live preflight is read-only and must report `"ok": true` with `target_state` METHOD_A. Record its digest:
  ```
  python experiments/ev_redistribution_c1/release/c1_release.py preflight --root "<repository root>"
  ```
  The preflight checks:
  - targets are the pinned Method A bytes;
  - the shadow files match their pinned hashes and rebuild byte-equal from the C1 LSOA source;
  - rows are 30/1,210/140 and there are five quarters;
  - DNO = LA, and each LAEP = the sum of its mapped LAs;
  - the expected totals, `metadata.json` `latest_actual_period` = 2026-03 and the archive (318 legacy rows) all match;
  - there are no incomplete releases and no staged leftovers, and paths are within the length limit;
  - every protected file is hashed. On 2026-10-12 that was 1,000+ files, among them `dashboard.html`, all forecasts, HP files, raw `ev/` and `lookups/`.
- [ ] The Method A archive is confirmed: `method_a_archive/manifest.json` sha256 `69e52ab9…`; history `e5b234c2…`; original copy `4878bba6…`, equal to the current production DNO file.
- [ ] Back up the whole repository folder outside the repo (a belt-and-braces copy, independent of the tool's own backup).

## D. Expected C1 totals (stock; checked by preflight and post-validation)

| Quarter | UKPN | EPN | LPN | SPN | BEV | PHEV |
|---|---|---|---|---|---|---|
| 2025-03 | 719,684 | 338,356 | 166,018 | 215,310 | 478,318 | 241,366 |
| 2025-06 | 759,063 | 359,833 | 172,310 | 226,920 | 502,907 | 256,156 |
| 2025-09 | 806,190 | 385,455 | 179,394 | 241,341 | 533,367 | 272,823 |
| 2025-12 | 846,833 | 407,129 | 185,240 | 254,464 | 561,408 | 285,425 |
| 2026-03 | 896,098 | 435,157 | 191,386 | 269,555 | 591,291 | 304,807 |

Method A March 2026 for contrast: 908,964. Rows: DNO 30, LA 1,210 (121 LAs), LAEP 140 (14 LAEPs).

## E. Exact allowlist

The tool may replace only these three files:
- `project/output_processed/integration/ev_actuals_dno.csv`
- `project/output_processed/integration/ev_actuals_la.csv`
- `project/output_processed/integration/ev_actuals_laep.csv`

It may also create staged `.c1promote-*` / `.c1rollback-*` files next to them (always removed afterwards) and write to `experiments/ev_redistribution_c1/release/state/`.

It never writes:
- `monthly_forecast_*` or `metadata.json`;
- `dashboard.html`;
- any HP, solar or charger file;
- `ev/`, `lookups/`, `dfes/`, `lct/` or `templates/`;
- anything else under `project/`.

`metadata.json` is **not** patched. Its `latest_actual_period` is already 2026-03, and C1 provenance is kept in the release manifest.

## F. Commands

Dry run, as validated in Phase 5. `<env>` is an absolute path outside the repository.

```
python experiments/ev_redistribution_c1/release/disposable_env.py <env>
python experiments/ev_redistribution_c1/release/c1_release.py preflight --root <env>
python experiments/ev_redistribution_c1/release/c1_release.py promote   --root <env> --release-id c1-YYYYMMDD-dryrun --confirm "PROMOTE c1-YYYYMMDD-dryrun <digest16>"
python experiments/ev_redistribution_c1/release/c1_release.py verify-c1 --root <env>
python experiments/ev_redistribution_c1/release/c1_release.py status    --root <env>
python experiments/ev_redistribution_c1/release/c1_release.py rollback  --root <env> --release-id c1-YYYYMMDD-dryrun --confirm "ROLLBACK c1-YYYYMMDD-dryrun <manifest16 from status>"
```

Live. Run only after A–C are complete **and** the approved change has set `LIVE_RELEASE_ENABLED = True`. Until then the tool refuses these commands.

```
python experiments/ev_redistribution_c1/release/c1_release.py promote --root "<repository root>" --release-id c1-YYYYMMDD-ukpn-ev --confirm "PROMOTE c1-YYYYMMDD-ukpn-ev <live preflight digest16>"
```

Rollback (approved): `rollback --root <repo> --release-id <id> --confirm "ROLLBACK <id> <manifest16>"`.

Interrupted or failed run: `recover --root <repo> --release-id <id> --confirm "RECOVER <id>"`. This always restores the original Method A bytes from the verified backup.

## G. Post-release validation

- [ ] The promote output shows `status: COMPLETE`, and `promotion_qa.pass` is true with `protected_changed: []`.
- [ ] `verify-c1 --root <repo>` exits 0. All three targets are C1, the first period is 2025-03, there are five periods, and UKPN March 2026 is 896,098.
- [ ] Run the dashboard regression suite against production with no redirect. EV values equal the shadow expectations; forecasts and non-EV views are unchanged.
- [ ] Browser check (manual):
  - EV charts for UKPN, EPN, Peterborough LA, Westminster LA, and the Norwich and Outer East LAEPs;
  - the HP LA/LAEP views still load;
  - Earliest Observation shows (2025-03).
- [ ] Commit the three C1 files on their own (LFS), so git no longer holds Method A as the latest version.
- [ ] Keep `release/state/` (manifests, backups) with the release record.

## H. Abort or roll back immediately if

- preflight is not `ok`, or its digest differs from the approved one;
- the tool reports `FAILED_*`, `RECOVERY_REQUIRED` or `LOCK_BUSY` for an unknown holder;
- `promotion_qa` lists any protected change, structure failure or leftover;
- `verify-c1` fails after release, or reports a Method A or mixed series at any later time;
- any forecast, HP, solar, charger or `metadata.json` hash differs from the pre-release inventory;
- the dashboard fails to load, an EV chart shows pre-2025 points, or a DFES/NESO value differs.
