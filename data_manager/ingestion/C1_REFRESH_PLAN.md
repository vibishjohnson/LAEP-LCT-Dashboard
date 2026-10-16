# Phase 13 proposal: C1 → C1 quarterly refresh

Status: **design only**. Nothing in this document has been run against production. The current release
tool deliberately refuses C1 → C1 (tested in Phase 11: `targets_state` fails, zero writes), and that
refusal must not be bypassed by re-pinning old hashes.

## 1. Where things stand

| Item | Current state |
|---|---|
| Live EV actuals | Release `c1-20261014-ukpn-ev`: five quarters 2025-03 … 2026-03, UKPN March 2026 = 896,098 |
| C1 input | `ev/df_VEH0135.csv` (DfT 2026 Q1 release; sha `ba54465c…`; BOM-prefixed re-save of DfT's file with one Scottish LSOA name re-encoded) |
| Method | `experiments/ev_redistribution_c1/c1_method.py`: private protected, company + unclassified share up to 100, national pool, same-quarter private weights, BEV/PHEV conservation, LPN floor 0.20 |
| Quarters | Hard-coded in `run_c1.py` (`QUARTERS`) and `release_config.EXPECTED_PERIODS` |
| Release pins | `release_config.py`: `SHADOW_SHA256`, `EXPECTED_ROWS` (30 / 1,210 / 140), `EXPECTED_TOTALS`, `METHOD_A_TARGET_SHA256` as the only accepted "before" state |
| Newest DfT data available | VEH0135, VEH0142 and VEH0105 for **2026 Q2** (downloaded 1 Oct 2026); ingestion validates all three |

## 2. Decisions needed before any build (not assumed here)

1. **Window — DECIDED (user, Phase 12B): expanding history, never rolling.** The live C1 series starts in
   March 2025 and keeps every subsequently approved quarter. Consequences for the next refresh:
   * March 2025 stays the first actual; Network/LA/LAEP chart opening baselines do not move.
   * June 2026 becomes the sixth actual quarter once approved; later quarters extend the series.
   * `EXPECTED_PERIODS` / `EXPECTED_ROWS` become per-release values in the sealed candidate
     (6 quarters → DNO 36, LA 1,452, LAEP 168 rows), never a fixed five.
   * Values published by earlier releases stay preserved in those releases' manifests, backups and git
     history; the source registry keeps every VEH0135 version (nothing is deleted or overwritten).
   * DfT restatements of earlier quarters are detected (ingestion comparison already reports
     restatement % per series) and must be reported and approved before publication.
   * Latest Actual (`metadata.json latest_actual_period`) advances only as part of an approved release.
2. **Restatements.** Each DfT release restates every earlier quarter. Choose one:
   * **(a)** recompute all window quarters from the newest vintage, which is consistent but changes published history;
   * **(b)** freeze the already-published quarters and add only the new quarter.

   Either way, the previous release's outputs and sources stay preserved and comparable.
3. **Source of the C1 input.** Recommended: C1 reads the staged, immutable registry version
   (`source_registry/versions/VEH0135/2026_Q2/…/original.csv`). `ev/df_VEH0135.csv` stays untouched,
   because `scripts/01_ev_processing.py` (body-type and SMMT nowcast workflow) also reads it. The
   alternative, replacing `ev/` as part of the release, couples two workflows.
4. **Approvals.** Named approver(s) and a release ticket. These were an open governance exception in Phase 11.

## 3. Repeatable workflow

1. **Detect the latest validated quarter.** Find the newest `STAGED` VEH0135 version whose quarters
   include the chosen window and that passed every check, including UKPN LSOA coverage. A VEH0142/VEH0105
   quarter mismatch is shown but does not block, because C1 does not read them.
2. **Create a C1 source set** (new, immutable): `c1_source_sets/<set_id>/manifest.json`, recording:
   * the VEH0135 version label and sha256;
   * `lookups/LSOA to DNO.csv`, `integration/geography_lookup.csv` and `LA-LAEP lookup.csv` hashes;
   * the window, the restatement policy and the previous release id.

   A source set never changes.
3. **Run Method C1 with identical methodology.**
   * Parameterise `run_c1.py` only through a source-set argument (input path, window). `c1_method.py` is unchanged and hash-pinned in the source-set manifest.
   * Write to `experiments/ev_redistribution_c1/refresh/<refresh_id>/outputs/`, never to `outputs/`.
   * **Methodology identity gate:** running the new entry point on the *current* source set (ev 2026 Q1, current window) must reproduce today's `c1_lsoa_quarterly.csv` and shadow files byte for byte.
4. **Generate LSOA, DNO, LA and LAEP datasets.** `integration_writer.py` writes
   `refresh/<id>/shadow_integration/` with a manifest giving:
   * the source set, method hash and floor 0.20;
   * the uplift (expected 0);
   * the periods, rows and totals.
5. **Compare with live C1** for every quarter × fuel × geography (DNO, 121 LAs, 14 LAEPs, 11,023 LSOAs):
   * restated quarters (delta and %), the new quarter, LPN share vs floor;
   * national BEV/PHEV conservation and hotspot LSOAs;
   * Network vs LA/LAEP consistency (DNO = Σ LA = Σ LSOA; LAEP = Σ mapped LAs).

   Thresholds go in a reviewed QA config; breaches block.
6. **Validate** the full QA suite (the 357 checks of the original candidate, generalised to the window)
   and the dashboard harness (75 views):
   * the candidate views must equal the harness output from the candidate shadow;
   * non-EV views must be unchanged;
   * Earliest Observation and Latest Actual must be as decided in step 2.
7. **Build a sealed release candidate:** `refresh/<id>/release_candidate.json`, sealed with sha256. It records:
   * the candidate target hashes, expected periods, rows and totals;
   * the source set;
   * the previous release id and that release's manifest sha256.
8. **C1 → C1 promotion and rollback** with the extended tool (section 4), in a disposable environment
   first. A live run happens only after approval and the separate `LIVE_RELEASE_ENABLED` change.

## 4. Release tool extension (specific changes)

| Area | Change |
|---|---|
| Expectations | Move per-release expectations (`SHADOW_SHA256`, `EXPECTED_PERIODS`, `EXPECTED_ROWS`, `EXPECTED_TOTALS`) out of `release_config.py` into the sealed `release_candidate.json`. `release_config.py` keeps only tool-level constants: methodology id, floor, live switch, target allowlist and protected trees. |
| Before-state | `preflight(require=...)` gains `C1_RELEASE:<release_id>`: targets must equal the `new_sha256` of that sealed, `COMPLETE` release, verified against its manifest sha256 and journal. The **latest** sealed release is the only acceptable before-state; an older C1 release is classified `C1_SUPERSEDED` and refused. |
| Classification | `classify_targets` reads every sealed release manifest and returns `C1@<id>` / `METHOD_A` / `UNKNOWN`. Method A is never an accepted before-state for a C1 → C1 promotion. |
| Promote | Unchanged mechanics (OS lock, journal, backup, digest-bound token, staged writes, verify). The token also binds the candidate seal and the previous release id. |
| Rollback | Rolls the newest release back to its recorded `old_sha256`, which is the previous C1. A chain of rollbacks is allowed one release at a time. Rolling back to Method A stays possible only through the original release's record and needs its own approval. |
| Guards | The pre-commit guard already accepts only the latest sealed release's bytes, so it extends naturally. Add a reversion-scan rule that flags a superseded C1 release in the working tree. |
| Metadata | `metadata.json` `latest_actual_period` changes. Include the approved re-pin (`repin_metadata.py`) in the candidate, not as a manual step. |
| Tests | All in disposable environments: <ul><li>C1(prev) → C1(new) promotion;</li><li>interrupted promote → recover;</li><li>rollback to C1(prev);</li><li>double promotion refused;</li><li>stale previous release refused;</li><li>Method A restoration still blocked;</li><li>a candidate with a different floor, methodology id or method hash refused;</li><li>source-set or seal tampering refused.</li></ul> |

## 5. Explicitly out of scope until approved

* Replacing `ev/` inputs.
* Re-running production promotion.
* Enabling `LIVE_RELEASE_ENABLED`.
* Changing the floor or the methodology.
* Re-pinning old hashes to force a promotion.
* Pushing to the remote.
