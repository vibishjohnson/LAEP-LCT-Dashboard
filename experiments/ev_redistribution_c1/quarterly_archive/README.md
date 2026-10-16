# C1 quarterly release archive

Each subdirectory is the version-controlled record of one approved C1 refresh candidate. CSVs are stored in Git LFS;
JSON, JSONL and Markdown are stored without line-ending conversion (the per-directory `.gitattributes`). Every file is
listed with its SHA-256 in `ARCHIVE_MANIFEST.json`, which is sealed.

| Path | Content |
|---|---|
| `source/VEH0135_2026Q2_original.csv` | the original DfT VEH0135 bytes, as validated by the ingestion pipeline |
| `source/*.json` | registry version manifest and validation profile; the sealed C1 source set |
| `baseline/` | source set of the live October release and its byte-for-byte reproduction report |
| `candidate/` | the sealed candidate: all-quarter LSOA output, both publication options, QA and restatement reports |
| `decision/` | the hash-chained decision log (option B and its restatement approval) and the candidate state |
| `package/` | the sealed option B release package consumed by `c1_quarterly_release.py` |

Not archived on purpose: dry-run reports and job files (they contain temporary paths), the ingestion database and
operator or host details.

```
python -m data_manager.c1_refresh.cli verify-archive    <this dir>/<candidate>
python -m data_manager.c1_refresh.cli restore-workspace <this dir>/<candidate> <new workspace dir>
python -m data_manager.c1_refresh.cli reproduce         <this dir>/<candidate>
```

`reproduce` regenerates the candidate from the archived source with the checked-out code. The October baseline uses
`ev/df_VEH0135.csv` from the repository. It then compares every data output with the archive.
