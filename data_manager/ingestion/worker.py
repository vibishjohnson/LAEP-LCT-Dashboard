"""
Background worker: identify, parse, validate, profile and compare one uploaded file.

Runs in its own process (python -m data_manager.ingestion.worker <job_id> <registry_root>) under
a memory cap, so a pathological file cannot take the web application down. Reads only the
upload, the read-only production source in ev/ (for comparison) and lookups/LSOA to DNO.csv.
Writes only inside the registry (work/<job>/, production_profiles/, failed/).
"""

import json
import os
import sys
import time
import traceback
from dataclasses import replace

from . import contracts, readers
from .archive_safety import UnsafeArchive
from .config import Settings
from .procmem import self_memory_mb
from .registry import Registry, sha256_file


class _Progress:
    """Throttled progress writer (at most twice a second)."""

    def __init__(self, reg, job_id, lo, hi, stage):
        self.reg, self.job_id, self.lo, self.hi, self.stage, self.t = reg, job_id, lo, hi, stage, 0.0

    def __call__(self, fraction):
        t = time.time()
        if t - self.t >= 0.5:
            self.t = t
            self.reg.update_job(self.job_id, progress=round(self.lo + (self.hi - self.lo) * min(1.0, fraction), 4), stage=self.stage)


def heads(path, fmt, sheets, settings, n=contracts.HEADER_SEARCH_ROWS):
    out = {}
    for s in sheets:
        rows = readers.iter_rows(path, fmt, s, settings)
        head = []
        try:
            for r in rows:
                head.append(r)
                if len(head) >= n:
                    break
        finally:
            rows.close() if hasattr(rows, "close") else None
        out[s] = head
    return out


def production_profile(reg, settings, contract, scope):
    """Profile of the current approved source (ev/<file>), cached by its hash. Read-only."""
    p = os.path.join(settings.production_ev_dir, contract.production_file)
    if not os.path.exists(p):
        return None
    sha = sha256_file(p)
    info = {"path": os.path.relpath(p, os.path.dirname(settings.production_ev_dir)).replace("\\", "/"), "sha256": sha,
            "size_bytes": os.path.getsize(p), "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p)))}
    cache = reg.path("production_profiles", f"{contract.dataset}_{sha}.json")
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            prof = json.load(f)
    else:
        rows = readers.iter_rows(p, "csv", None, settings)
        ck, prof, _, _ = contracts.validate_and_profile(rows, contract, scope, None, 0, settings.max_data_rows)
        prof["validation"] = {"errors": [e["detail"] for e in ck.errors], "warnings": [w["detail"] for w in ck.warnings]}
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(prof, f, default=str)
    info.update(latest_quarter=prof.get("latest_quarter"), rows=prof.get("rows"))
    return info, prof


def run(job_id, settings):
    reg = Registry(settings)
    reg.ensure()
    job = reg.job(job_id)
    upload = reg.upload_path(job_id)
    work = reg.work_dir(job_id)
    os.makedirs(work, exist_ok=True)
    t0 = time.time()
    timings = {}
    result = {"job_id": job_id, "original_filename": job["original_filename"], "sha256": job["sha256"],
              "size_bytes": job["received_bytes"]}

    def save(status):
        result["status"] = status
        result["timings_s"] = timings
        result["memory_mb"] = dict(zip(("working_set", "peak_working_set", "peak_commit"), self_memory_mb()))
        tmp = os.path.join(work, "result.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=1, default=str)
        os.replace(tmp, os.path.join(work, "result.json"))

    stage = "identify"
    try:
        reg.update_job(job_id, state="PARSING", stage=stage, progress=0.0, attempts=(job["attempts"] or 0) + 1, error=None)
        fmt = readers.sniff_format(upload, job["original_filename"])
        result["format"] = fmt
        reg.update_job(job_id, format=fmt)
        sheets = readers.sheet_names(upload, fmt, settings, _Progress(reg, job_id, 0.0, 0.1, "identify"))
        result["sheets"] = sheets
        wanted_sheet = job["sheet_override"]
        if wanted_sheet and wanted_sheet not in sheets:
            raise readers.ParseError(f"worksheet {wanted_sheet!r} not found (sheets: {sheets})")
        hb = heads(upload, fmt, [wanted_sheet] if wanted_sheet else sheets, settings)
        candidates = contracts.identify(hb, job["original_filename"])
        result["identification"] = {"candidates": candidates, "override": job["dataset_override"]}
        timings["identify"] = round(time.time() - t0, 2)
        if job["dataset_override"]:
            contract = contracts.CONTRACTS[job["dataset_override"]]
            match = [c for c in candidates if c["dataset"] == contract.dataset]
            sheet = match[0]["sheet"] if match else (wanted_sheet or sheets[-1])
            result["identification"]["method"] = "reviewer override"
        else:
            if not candidates:
                result["head"] = {str(s): h[:8] for s, h in hb.items()}
                raise readers.ParseError("not recognised as VEH0135, VEH0142, VEH0105 or VEH0125: no worksheet has the "
                                         "expected header row within its first 40 rows")
            best = candidates[0]
            contract, sheet = contracts.CONTRACTS[best["dataset"]], best["sheet"]
            rivals = [c for c in candidates[1:] if c["dataset"] != best["dataset"] and c["score"] == best["score"]]
            result["identification"]["method"] = "content (header, worksheet, title rows)"
            result["identification"]["ambiguous_with"] = [r["dataset"] for r in rivals]
        if fmt != "csv" and contract.sheet_names and sheet not in contract.sheet_names:
            result.setdefault("notes", []).append(f"worksheet {sheet!r} is not the published name {list(contract.sheet_names)}")
        result.update(dataset=contract.dataset, sheet=sheet, dataset_title=contract.title, dataset_role=contract.role)
        reg.update_job(job_id, dataset=contract.dataset, sheet=sheet)

        stage = "validate"
        reg.update_job(job_id, state="VALIDATING", stage=stage, progress=0.1)
        scope = contracts.UkpnScope(settings.lsoa_dno_lookup)
        t1 = time.time()
        canonical = os.path.join(work, "canonical.csv")
        rows = readers.iter_rows(upload, fmt, sheet, settings, _Progress(reg, job_id, 0.1, 0.85, "validate"))
        ck, profile, preview, canonical_sha = contracts.validate_and_profile(
            rows, contract, scope, canonical, settings.preview_rows, settings.max_data_rows)
        timings["parse_validate"] = round(time.time() - t1, 2)
        result.update(checks=ck.items, profile=profile, preview=preview, canonical_sha256=canonical_sha)
        if profile:
            reg.update_job(job_id, period_label=profile.get("latest_quarter"))

        stage = "compare"
        reg.update_job(job_id, stage=stage, progress=0.88)
        t2 = time.time()
        flags = []
        if settings.compare_with_production and profile:
            prod = production_profile(reg, settings, contract, scope)
            if prod:
                info, pprof = prod
                result["production"] = info
                result["comparison"] = contracts.compare_profiles(profile, pprof)
                flags += contracts.comparison_warnings(result["comparison"])
                if info["sha256"] == job["sha256"]:
                    flags.append("identical to the current production source")
        version, other_job = reg.find_by_sha(job["sha256"])
        if version:
            flags.append(f"identical file already stored as {version['label']}")
        if other_job and other_job["id"] != job_id:
            flags.append(f"identical file also uploaded as {other_job['id']} ({other_job['state']})")
        if result["identification"].get("ambiguous_with"):
            flags.append(f"identification ambiguous with {result['identification']['ambiguous_with']}; confirm before saving")
        flags += [f"{w['check']}: {w['detail']}" for w in ck.warnings]
        result["flags"] = flags
        timings["compare"] = round(time.time() - t2, 2)
        timings["total"] = round(time.time() - t0, 2)
        if ck.errors:
            save("INVALID")
            reg.finish_job(job_id, "FAILED", stage="validate", progress=1.0,
                           error="; ".join(f"{e['check']}: {e['detail']}" for e in ck.errors)[:2000])
        else:
            save("VALID")
            reg.finish_job(job_id, "READY", stage="review", progress=1.0,
                           message=f"{contract.dataset} {profile['latest_quarter']}: {profile['rows']:,} rows validated")
        return 0
    except (UnsafeArchive, readers.UnsupportedFormat) as e:
        # Not safe or not useful to keep: delete the bytes, keep the error record.
        result["error"] = f"{type(e).__name__}: {e}"
        save("REJECTED_UNSAFE" if isinstance(e, UnsafeArchive) else "UNSUPPORTED")
        reg.keep_failed(job_id, result["error"], None)
        if os.path.exists(upload):
            os.remove(upload)
        reg.finish_job(job_id, "FAILED", stage=stage, error=result["error"])
        return 2
    except Exception as e:  # noqa: BLE001 - every failure is recorded, never raised into the web app
        result["error"] = f"{type(e).__name__}: {e}"
        result["traceback"] = traceback.format_exc()[-4000:]
        save("ERROR")
        reg.finish_job(job_id, "FAILED", stage=stage, error=result["error"][:2000])
        return 1


def main(argv):
    job_id, root = argv[1], argv[2]
    overrides = json.loads(argv[3]) if len(argv) > 3 else {}
    settings = replace(Settings(), registry_root=root, **overrides)
    return run(job_id, settings)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
