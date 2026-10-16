# DfT source ingestion (Phase 12)

Upload → Identify → Validate → Preview → Save to staging, for the DfT vehicle licensing tables used by the
LAEP LCT Dashboard. Pages: `/dft-ingest` (upload and review) and `/dft-sources` (version history).

**Staging is not publication.** Nothing here writes to `ev/`, `project/output_processed/`, the C1 release
state or the dashboard. The approved source for each table is still the file in `ev/`; replacing it is
part of a separate, authorised C1 quarterly refresh (see `C1_REFRESH_PLAN.md`).

## Why the old upload failed

* The **DfT Update** page never sent files to the server. Its JavaScript showed hard-coded results ("PASS",
  "329,029 rows", "2026 Q1") whatever was chosen, and its file picker only accepted `.csv` and `.xlsx`.
* No Flask route accepted uploads. `DfTUpdateManager` and `DfTNormalizer` were imported but never called.
  If wired in, they would have failed on ODS: `pd.read_csv` on a zip, then whole-file reads.
  `run_ev_processing` deletes the production `ev/` inputs and replaces them with symlinks, which fails on
  Windows without symlink privilege and so loses the originals. It must not be used.
* **Parsing, not HTTP, is the limit for ODS.** The real DfT workbooks are small on disk but large inside:
  `veh0142.ods` holds a 241 MB `content.xml` and `veh0105.ods` a 412 MB one. pandas' `odf` engine (odfpy)
  builds a full DOM. On the real 2026 Q2 VEH0142 file it raised `MemoryError` at a 1.4 GB cap and again
  at a 2.6 GB cap. This machine typically has 2–3 GB free.
* Flask itself had no `MAX_CONTENT_LENGTH`. The 500 KB `MAX_FORM_MEMORY_SIZE` in Flask 3.1 applies only to
  non-file form fields. So HTTP size limits were not the cause.

## Architecture

| Module | Responsibility |
|---|---|
| `config.py` | Limits and locations (environment-overridable). |
| `web.py` | Blueprint: pre-check, streamed `PUT /api/ingest/upload`, status, review actions, pages. |
| `jobs.py` | One dispatcher thread, a bounded queue (4), one worker **process** at a time with a memory cap (Windows job object / `RLIMIT_AS`) and a timeout. |
| `worker.py` | Identify → parse → validate → profile → compare. Writes only inside the registry. |
| `readers.py` | Streaming CSV / XLSX (openpyxl read-only) / ODS (`iterparse` over `content.xml`; one row in memory at a time). |
| `archive_safety.py` | Zip checks before any parser runs: bombs, traversal, encryption, nesting, member and total size limits. |
| `contracts.py` | The real VEH0135 / VEH0142 / VEH0105 / VEH0125 layouts; identification, validation, UKPN scope, profile, comparison. |
| `registry.py` | SQLite (WAL) jobs/versions/events plus an immutable file store; staging, supersession, restatements, recovery. |
| `procmem.py` | Process memory measurement and the worker memory cap. |

### Upload

* The page calls `POST /api/ingest/precheck` first.
* It then streams the file with `PUT /api/ingest/upload?filename=…`, raw body, with progress shown.
* The server:
  1. refuses before reading any bytes when the request breaks a limit:
     | Response | Reason |
     |---|---|
     | 400 | file type |
     | 411 | no `Content-Length` |
     | 413 | over the size limit |
     | 429 | queue full, or too many concurrent uploads |
  2. writes 1 MB chunks to `incoming/<job>.part`, hashing as it goes;
  3. discards the `.part` file if the body is short (interrupted);
  4. rejects exact duplicates of stored versions or in-flight uploads (409, checked under a lock);
  5. queues the job.

The Flask process stays flat: +3 to +5 MB while uploading 245 MB and 978 MB files.

### States
`UPLOADING → QUEUED → PARSING → VALIDATING → READY` (validation passed: review) or `FAILED` (with the
stage: upload / identify / validate / duplicate / worker). From review a file can become `STAGED`
(saved) or `REJECTED`.

From review the user can also:
* **Re-validate as…** another dataset and/or worksheet, to correct a wrong identification;
* **Retry**, after an interrupted or failed parse.

On start-up, in-flight jobs become `FAILED`: interrupted uploads are deleted, and interrupted parses can be retried.

### Identification and validation

* **Identification uses the content:** header row (within the first 40 rows), worksheet name and title rows. The filename is only a weak hint.
* **VEH0142 vs VEH0105:** their headers are identical, so the worksheet name and title rows decide. A wrong forced classification fails on `Units` (Number vs Thousands) and the required Fuel values.

The checks, all on every row:
* header found;
* quarter labels valid, contiguous and newest first;
* minimum rows;
* unique key, using real keys:
  * VEH0135: `LSOA21CD, Fuel, Keepership`;
  * VEH0142 and VEH0105: `BodyType, Fuel, Keepership, ONS Sort`;
* Units;
* no values beyond the header;
* geography codes valid, with `Miscellaneous` and `[z]` accepted as published placeholders;
* every quarter cell numeric, or a DfT marker `[c] [x] [z] [low] [u] [w]`;
* whole-number counts, except VEH0105 (thousands);
* required categories present (the C1 fuels and keeperships for VEH0135);
* latest quarter populated;
* UKPN-scope rows present.

Values are kept exactly as published:
* codes, names and leading spaces or zeros are strings;
* ODS and XLSX numbers are the stored value (`8235`, `0.426`), not the display text (`8,235`, `0.43`).

### UKPN scope (CLAUDE.md)

The profile reports total rows, rows in EPN/LPN/SPN scope, rows outside, and the field used:
* **LSOA tables:** `LSOA21CD` → `Majority Licence area` (`lookups/LSOA to DNO.csv`).
* **LA tables:** `ONS Code` ∈ the LAD22CD set of UKPN LSOAs.

All comparisons use the UKPN series.

### Comparison with production
For each series, the comparison shows:
* the production value at its latest quarter;
* the candidate's value for that same quarter (restatement %);
* the candidate's new quarter (change %).

Row counts and category counts are compared too. Review flags (non-blocking) cover:
* large row or category changes;
* restatements above 1%;
* an older quarter;
* a file identical to production.

## Versioned source store (`source_registry/`, git-ignored by its own `.gitignore`)

```
registry.db
incoming/<job>.upload
work/<job>/result.json, canonical.csv
versions/<DATASET>/<YYYY_Qn>/<DATASET>-<YYYYQn>-v<n>/original.<ext>  canonical.csv  profile.json  manifest.json
failed/<job>/error.json [+ original.bin when a well-formed file failed to parse and is ≤ 300 MB]
production_profiles/<DATASET>_<sha256>.json
```

**How a version is saved:**
* A row is reserved under `BEGIN IMMEDIATE`.
* Files are written to `<label>.tmp/`, hash-verified, renamed into place, made read-only, then marked `STAGED`.
* A crash leaves `PENDING_FILES`, which start-up recovery resolves to `STAGED` (manifest verifies) or `FAILED_STAGING`.

**Staging rules:**
* The reviewer must confirm the dataset and period.
* Identical bytes are refused.
* An older quarter than production, or than an existing staged version, is refused.
* Another file for a quarter that is already staged, or equal to production's latest, is refused as a **conflicting reporting period** unless a restatement reason is given. It is then recorded with `restates_version_id`.
* Every version records `supersedes_version_id` (the previous version of that dataset).
* Nothing is ever overwritten or deleted.

**Unsafe or unsupported uploads** are deleted. Their error record is kept.

## Limits (environment variables)

| Setting | Default | Variable |
|---|---|---|
| Upload size | 1024 MB | `LCT_INGEST_MAX_UPLOAD_MB` |
| Uncompressed spreadsheet member | 2048 MB | `LCT_INGEST_MAX_XML_MB` |
| Compression ratio (members > 1 MB) | 200:1 | — |
| Worker memory cap | 1536 MB | `LCT_INGEST_WORKER_MEMORY_MB` |
| Worker time limit | 1800 s | `LCT_INGEST_WORKER_TIMEOUT_S` |
| Registry location | `<repo>/source_registry` | `LCT_SOURCE_REGISTRY` |
| Concurrent uploads / queued jobs | 2 / 4 | — |

## Measured on this machine (real DfT files, 14 Oct 2026)

| File | Size | Worker peak commit | Time | Flask growth |
|---|---|---|---|---|
| veh0142.ods 2026 Q2 (241 MB XML, 26,156 rows) | 7.5 MB | 79 MB | 30 s | +4 MB |
| veh0105.ods 2026 Q2 (412 MB XML, 40,759 rows) | 17.6 MB | 79 MB | 43 s | +4 MB |
| df_VEH0125.csv 2026 Q2 (245 MB) | 245 MB | 130 MB | 47 s (upload 1.3 s) | +3 MB |
| synthetic 4× VEH0125 (978 MB, 3.59 M rows) | 978 MB | 130 MB | 200 s (upload 5 s) | +5 MB |
| pandas `odf` engine, veh0142.ods | — | > 2.6 GB, MemoryError | — | — |

Workers set `OPENBLAS_NUM_THREADS=1`. Importing pandas (through `data_manager/__init__`) otherwise commits about 400 MB of OpenBLAS buffers.

## Tests

`python -m pytest tests/ingestion -p no:cacheprovider`.

* **Destructive tests** use pytest temporary directories only.
* **`test_real_files.py`** reads the real DfT downloads on this machine and is skipped where they are absent.
