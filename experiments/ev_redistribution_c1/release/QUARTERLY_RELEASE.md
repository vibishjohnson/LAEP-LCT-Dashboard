# C1 quarterly release (C1 → C1)

`c1_quarterly_release.py` publishes a sealed, approved C1 refresh candidate over the current C1 release. It extends
`c1_release.py` (the October Method-A-to-C1 tool) and reuses its safety primitives unchanged: path safety, the OS
release lock, atomic replace, sealed manifests, the metadata re-pin log, fault injection and the live switch.

## Policy (approved)

- C1 history starts in **March 2025** and only grows: no rolling window, no gaps.
- New DfT vintages may revise earlier quarters. Published values change only through **option B**, with a recorded
  restatement approval bound to the sealed revision report.
- The methodology (`C1_DYNAMIC_PRIVATE_EV`) and the **0.20** LPN floor are unchanged.

## Write scope (four files, nothing else)

| File | Change |
|---|---|
| `project/output_processed/integration/ev_actuals_dno.csv` | option B bytes from the package |
| `project/output_processed/integration/ev_actuals_la.csv` | option B bytes from the package |
| `project/output_processed/integration/ev_actuals_laep.csv` | option B bytes from the package |
| `project/output_processed/integration/metadata.json` | `latest_actual_period` only, from the old last quarter to the new one |

Every file is backed up, recorded in the release manifest, and restored by rollback or recovery. The metadata hash change is
recorded in the hash-chained re-pin log.

## Release chain

- **The current release:** the single COMPLETE release whose published hashes equal the live files.
- **Manifests:** quarterly manifests (`schema c1-quarterly-release/1`) sit next to the October manifest in
  `state/releases/`, so the pre-commit guard follows the newest release.
- **Rollback:** restores only the immediately preceding release. The October release is never rolled back by this tool,
  because that would restore Method A.
- **Monitors:** after a quarterly release, use `verify-current`. The October `verify-c1` is a Method A reversion detector
  for the five-quarter release and will report newer releases as unrecognised.

## Inputs

| Input | Location |
|---|---|
| Sealed candidate, source bytes, source set, decision log, package, baseline reproduction | `experiments/ev_redistribution_c1/quarterly_archive/<candidate>/` (Git LFS for CSV) |
| Release authorisation | a JSON file kept **outside** the repository. Use `authorisation_template_C1C-2026Q2.json` and fill every field from the real approval record |

## Commands

```
python -m data_manager.c1_refresh.cli verify-archive experiments/ev_redistribution_c1/quarterly_archive/<candidate>
python -m data_manager.c1_refresh.cli reproduce      experiments/ev_redistribution_c1/quarterly_archive/<candidate>
python -m data_manager.c1_refresh.cli dry-run <candidate>          # disposable environment, DRY_RUN authorisation

python experiments/ev_redistribution_c1/release/c1_quarterly_release.py status         --root <repo>
python experiments/ev_redistribution_c1/release/c1_quarterly_release.py preflight      --root <repo> --package <archive>/package --authorisation <auth.json> --release-id <id>
python experiments/ev_redistribution_c1/release/c1_quarterly_release.py promote        ... --confirm "PROMOTE <id> <first 16 of the preflight digest>"
python experiments/ev_redistribution_c1/release/c1_quarterly_release.py verify-current --root <repo>
python experiments/ev_redistribution_c1/release/c1_quarterly_release.py rollback       --root <repo> --release-id <id> --confirm "ROLLBACK <id> <first 16 of manifest_sha256>"
python experiments/ev_redistribution_c1/release/c1_quarterly_release.py recover        --root <repo> --release-id <id> --confirm "RECOVER <id>"
```

## Authorisation gate

**A `PRODUCTION` authorisation must bind:**
- the release ID, package (ID and seal), candidate, source set, option B, restatement report, decision record and previous release;
- the exact four-file target allowlist;
- a named operator and a named approver, an approval reference and timestamp;
- `acknowledged_revised_history: true`;
- an empty `outstanding_governance` list.

**Refusals:**
- A `DRY_RUN` authorisation is refused on the live repository.
- The live repository also requires `LIVE_RELEASE_ENABLED = True`, set by a separate, approved change for the release window.

## Production steps (each needs separate approval)

1. Review and commit the release code, and push the archive (Git LFS) to the governed remote.
2. Fill the authorisation from the real approval record and keep it outside the repository.
3. Set `LIVE_RELEASE_ENABLED = True` (approved change).
4. Run `preflight` on the repository root and check every check passes.
5. Run `promote` with the exact confirmation string, then `verify-current`.
6. Set `LIVE_RELEASE_ENABLED = False` again.
7. Commit the four published files. The pre-commit guard accepts exactly the new release bytes.
8. If anything is wrong, run `rollback` (or `recover` for an interrupted operation) with the release ID.
