"""
Phase 5: Production Promotion Manager

Promotion of validated candidates to production with:
- Backup before any changes
- Eligibility validation
- File-level replacement of the promotion-managed files only
- Post-promotion validation
- Automatic rollback on failure
- Manual rollback capability

C1 safeguards:
- The dashboard EV actuals (integration/ev_actuals_*.csv) are published only by the C1 release tool
  (experiments/ev_redistribution_c1/release/c1_release.py). Promotion never writes them, rejects candidates that
  carry different ones, and verifies their bytes are unchanged before and after.
- DFES/NESO forecast rows must be unchanged by a promotion; only SMMT_NOWCAST rows may be added or replaced.
- Quarantined candidates (QUARANTINED.json in the run directory) are never eligible.
- Promotion replaces only PROMOTION_MANAGED_FILES, one file at a time; nothing else in output_processed is
  copied, moved or deleted.
- Rollback restores only the managed files, one at a time, from a verified (format 2) backup. Historic backups
  without per-file hashes are refused rather than restored.
- One promotion/rollback at a time (OS file lock, released automatically if the process dies). Each operation keeps
  a journal; an interrupted operation blocks further promotions until it is recovered with ManualRollback.rollback.
"""

import os
import shutil
import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import pandas as pd


# EV actuals are published only by the C1 release tool. General promotion and rollback must never replace them.
C1_PROTECTED_FILES = (
    "integration/ev_actuals_dno.csv",
    "integration/ev_actuals_la.csv",
    "integration/ev_actuals_laep.csv",
)
FORECAST_FILES = (
    "integration/monthly_forecast_dno.csv",
    "integration/monthly_forecast_la.csv",
    "integration/monthly_forecast_laep.csv",
)
# The only files promotion may change; rollback restores only these, file by file.
PROMOTION_MANAGED_FILES = (
    "dashboard_comparison_dno_monthly.csv",
    "dashboard_comparison_lsoa.csv",
    "dashboard_comparison_la.csv",
    "dashboard_comparison_laep.csv",
    *FORECAST_FILES,
    "integration/metadata.json",
)
BACKUP_FORMAT_VERSION = 2
QUARANTINE_MARKER = "QUARANTINED.json"
JOURNAL = "journal.json"
LOCK_FILE = ".promotion.lock"
INCOMPLETE = ("IN_PROGRESS", "RECOVERY_REQUIRED")


def _sha256(file_path: str) -> str:
    digest = hashlib.sha256()
    with open(file_path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _hashes(root: str, rel_paths) -> Dict[str, str]:
    return {p: _sha256(os.path.join(root, p)) for p in rel_paths if os.path.exists(os.path.join(root, p))}


def _write_json_atomic(path: str, obj: Dict):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _replace_file(src: str, dst: str, expected: str):
    """Replace dst with a verified copy of src via a temporary file in the same directory."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".promote_tmp"
    shutil.copy2(src, tmp)
    if _sha256(tmp) != expected:
        os.remove(tmp)
        raise Exception(f"copy verification failed for {dst}")
    os.replace(tmp, dst)


def _after_replace(rel_path: str):
    """Test hook: called after each file is replaced (no-op in production)."""


def forecast_rows_preserved(prod_path: str, cand_path: str) -> Tuple[bool, str]:
    """DFES/NESO rows must be identical to production; only SMMT_NOWCAST rows may be added or replaced."""
    if not os.path.exists(prod_path) or not os.path.exists(cand_path):
        return True, "not present"
    # Read as text so added SMMT rows cannot change inferred dtypes; compare values with a round-off tolerance.
    prod = pd.read_csv(prod_path, dtype=str, keep_default_na=False)
    cand = pd.read_csv(cand_path, dtype=str, keep_default_na=False)
    if list(prod.columns) != list(cand.columns):
        return False, f"{cand_path}: columns differ from production"
    cols = list(prod.columns)
    key = [c for c in cols if c != "value"]

    def base(d):
        d = d[d["forecast_source"] != "SMMT_NOWCAST"].copy()
        for c in key:
            num = pd.to_numeric(d[c], errors="coerce")
            if num.notna().sum() == (d[c] != "").sum():
                d[c] = num.map(lambda v: "" if pd.isna(v) else repr(float(v)))
        return d.sort_values(key).reset_index(drop=True)

    bp, bc = base(prod), base(cand)
    if len(bp) != len(bc) or not bp[key].equals(bc[key]):
        return False, f"{cand_path}: DFES/NESO forecast rows differ from production"
    if bc.duplicated(key).any():
        return False, f"{cand_path}: duplicate DFES/NESO rows"
    vp, vc = pd.to_numeric(bp["value"], errors="coerce"), pd.to_numeric(bc["value"], errors="coerce")
    same = (vp.isna() & vc.isna()) | ((vp - vc).abs() <= 1e-9 * vp.abs().clip(lower=1.0))
    if not same.all():
        return False, f"{cand_path}: DFES/NESO forecast values differ from production"
    smmt = cand[cand["forecast_source"] == "SMMT_NOWCAST"]
    if smmt.duplicated(key).any():
        return False, f"{cand_path}: duplicate SMMT_NOWCAST rows"
    return True, "DFES/NESO rows unchanged"


# ---------------------------------------------------------------------------- locking and journals
if os.name == "nt":
    import msvcrt

    def _try_lock(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(fd):
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd):
        fcntl.flock(fd, fcntl.LOCK_UN)


class PromotionBusy(Exception):
    """Another promotion or rollback holds the lock."""


class PromotionLock:
    """OS byte-range lock; released automatically if the holding process exits or is killed."""

    def __init__(self, backup_root: str):
        os.makedirs(backup_root, exist_ok=True)
        self.path = os.path.join(backup_root, LOCK_FILE)
        self.fd = None

    def __enter__(self):
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            _try_lock(fd)
        except OSError:
            os.close(fd)
            raise PromotionBusy("another promotion or rollback is in progress")
        self.fd = fd
        return self

    def __exit__(self, *exc):
        try:
            _unlock(self.fd)
        finally:
            os.close(self.fd)
        return False


def read_journal(backup_dir: str) -> Optional[Dict]:
    path = os.path.join(backup_dir, JOURNAL)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"status": "RECOVERY_REQUIRED", "error": "unreadable journal"}


def incomplete_operations(backup_root: str) -> List[Dict]:
    """Backups whose promotion/rollback journal shows an operation that did not finish."""
    out = []
    if not os.path.isdir(backup_root):
        return out
    for name in sorted(os.listdir(backup_root)):
        journal = read_journal(os.path.join(backup_root, name))
        if journal and journal.get("status") in INCOMPLETE:
            out.append({"backup_id": name, **journal})
    return out


class ProductionBackup:
    """Manages production backups for safe rollback."""

    def __init__(self, project_root: str):
        self.project_root = project_root
        self.backup_root = os.path.join(project_root, "project", "production_backups")
        os.makedirs(self.backup_root, exist_ok=True)

    def create_backup(self, run_id: str, old_vintage: str, new_vintage: str) -> Tuple[bool, str]:
        """
        Back up the promotion-managed files (with per-file hashes) before any change.

        Returns: (success, backup_id)
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_id = f"promotion_{timestamp}_{run_id}"
        backup_dir = os.path.join(self.backup_root, backup_id)

        try:
            if os.path.exists(backup_dir):
                return False, f"Backup {backup_id} already exists"
            os.makedirs(backup_dir)

            prod_output = os.path.join(self.project_root, "project", "output_processed")
            backup_output = os.path.join(backup_dir, "output_processed")

            managed = {}
            for rel_path in PROMOTION_MANAGED_FILES:
                prod_file = os.path.join(prod_output, rel_path)
                if not os.path.exists(prod_file):
                    continue
                backup_file = os.path.join(backup_output, rel_path)
                os.makedirs(os.path.dirname(backup_file), exist_ok=True)
                shutil.copy2(prod_file, backup_file)
                expected = _sha256(prod_file)
                if _sha256(backup_file) != expected:
                    return False, f"Backup verification failed for {rel_path}"
                managed[rel_path] = expected

            manifest = {
                "backup_id": backup_id,
                "run_id": run_id,
                "timestamp": datetime.now().isoformat(),
                "old_vintage": old_vintage,
                "new_vintage": new_vintage,
                "format_version": BACKUP_FORMAT_VERSION,
                "managed_files_sha256": managed,
                "c1_protected_sha256": _hashes(prod_output, C1_PROTECTED_FILES),
                "files_backed_up": sorted(managed),
            }
            _write_json_atomic(os.path.join(backup_dir, "manifest.json"), manifest)
            return True, backup_id

        except Exception as e:
            return False, str(e)

    def verify_backup(self, backup_id: str) -> Tuple[bool, str, Dict[str, str]]:
        """Check a backup can be used for file-scoped restore. Returns (ok, message, managed_files_sha256)."""
        backup_dir = os.path.join(self.backup_root, backup_id)
        if not backup_id or os.path.basename(backup_id) != backup_id or not os.path.isdir(backup_dir):
            return False, f"Backup {backup_id} not found", {}
        manifest_path = os.path.join(backup_dir, "manifest.json")
        try:
            with open(manifest_path, 'r') as f:
                manifest = json.load(f)
        except (OSError, ValueError) as e:
            return False, f"Backup {backup_id} manifest missing or unreadable: {e}", {}
        if manifest.get("format_version") != BACKUP_FORMAT_VERSION or "managed_files_sha256" not in manifest:
            return False, (f"Backup {backup_id} uses the historic format (no per-file hashes); its integrity cannot be "
                           "verified, so file-scoped rollback is refused. Restore manually after review."), {}
        managed = manifest["managed_files_sha256"]
        unknown = [p for p in managed if p not in PROMOTION_MANAGED_FILES]
        if unknown:
            return False, f"Backup {backup_id} lists files outside the managed set: {unknown}", {}
        if not managed:
            return False, f"Backup {backup_id} contains no managed files", {}
        backup_output = os.path.join(backup_dir, "output_processed")
        for rel_path, expected in managed.items():
            backup_file = os.path.join(backup_output, rel_path)
            if not os.path.exists(backup_file):
                return False, f"Backup {backup_id} is incomplete: {rel_path} missing", {}
            if _sha256(backup_file) != expected:
                return False, f"Backup {backup_id} is corrupt: {rel_path} does not match its recorded hash", {}
        return True, "verified", managed

    def restore_backup(self, backup_id: str) -> Tuple[bool, str]:
        """Restore promotion-managed files from a verified backup, one file at a time.

        Never deletes or replaces the production directory, never touches files outside
        PROMOTION_MANAGED_FILES, and never touches the C1 EV actuals.
        """
        try:
            ok, message, managed = self.verify_backup(backup_id)
            if not ok:
                return False, message

            backup_output = os.path.join(self.backup_root, backup_id, "output_processed")
            prod_output = os.path.join(self.project_root, "project", "output_processed")
            restored = []
            for rel_path, expected in managed.items():
                _replace_file(os.path.join(backup_output, rel_path), os.path.join(prod_output, rel_path), expected)
                restored.append(rel_path)

            mismatched = [p for p, h in managed.items() if _sha256(os.path.join(prod_output, p)) != h]
            if mismatched:
                return False, f"Restore verification failed for {mismatched}"
            return True, f"Restored {len(restored)} promotion-managed files from {backup_id}"

        except Exception as e:
            return False, str(e)

    def _list_backed_up_files(self, backup_dir: str) -> List[str]:
        """List files in backup directory."""
        files = []
        for root, dirs, filenames in os.walk(backup_dir):
            for f in filenames:
                rel_path = os.path.relpath(os.path.join(root, f), backup_dir)
                files.append(rel_path)
        return files


class CandidateValidator:
    """Validates that a candidate is eligible for promotion."""

    def __init__(self, project_root: str):
        self.project_root = project_root

    def can_promote(self, run_id: str) -> Tuple[bool, List[str]]:
        """
        Check if candidate can be promoted.

        Returns: (can_promote, list_of_reasons_if_not)
        """
        errors = []

        # Check candidate exists
        staging_dir = os.path.join(self.project_root, "project", "dft_staging", f"run_{run_id}")
        if not os.path.exists(staging_dir):
            errors.append(f"Candidate run {run_id} not found")
            return False, errors

        # Quarantined candidates are never eligible
        if os.path.exists(os.path.join(staging_dir, QUARANTINE_MARKER)):
            errors.append(f"Candidate {run_id} is quarantined ({QUARANTINE_MARKER})")
            return False, errors

        # Check if marked as SYNTHETIC/TEST
        if "SYNTHETIC" in run_id.upper() or "TEST" in run_id.upper():
            errors.append("Cannot promote synthetic/test candidate")
            return False, errors

        # Check candidate output exists
        candidate_output = os.path.join(staging_dir, "outputs", "lsoa_ev_bodytype.csv")
        if not os.path.exists(candidate_output):
            errors.append("Candidate output lsoa_ev_bodytype.csv not found")
            return False, errors

        # Verify QA validation
        qa_file = os.path.join(staging_dir, "qa_report.md")
        if os.path.exists(qa_file):
            with open(qa_file, 'r') as f:
                qa_content = f.read()
                if "FAIL" in qa_content.upper() or "ERROR" in qa_content.upper():
                    errors.append("Candidate has QA failures")
                    return False, errors

        # EV actuals are published only by the C1 release tool
        prod_output = os.path.join(self.project_root, "project", "output_processed")
        for file_path in C1_PROTECTED_FILES:
            cand_file = os.path.join(staging_dir, "outputs", file_path)
            prod_file = os.path.join(prod_output, file_path)
            if os.path.exists(cand_file) and (not os.path.exists(prod_file) or _sha256(cand_file) != _sha256(prod_file)):
                errors.append(f"Candidate carries EV actuals {file_path}; EV actuals are published only by the C1 release tool")
        if errors:
            return False, errors

        return True, []

    def get_candidate_metadata(self, run_id: str) -> Optional[Dict]:
        """Get candidate metadata."""
        staging_dir = os.path.join(self.project_root, "project", "dft_staging", f"run_{run_id}")
        metadata_path = os.path.join(staging_dir, "metadata.json")

        if os.path.exists(metadata_path):
            with open(metadata_path, 'r') as f:
                return json.load(f)

        return None


def candidate_status(project_root: str, run_id: str) -> Dict:
    """Display status derived from the real eligibility checks (for admin pages)."""
    ok, errors = CandidateValidator(project_root).can_promote(run_id)
    if ok:
        return {"status": "READY FOR PROMOTION", "can_promote": True, "errors": []}
    if any("quarantined" in e for e in errors):
        return {"status": "INELIGIBLE — QUARANTINED", "can_promote": False, "errors": errors}
    return {"status": "INELIGIBLE", "can_promote": False, "errors": errors}


class AtomicPromotion:
    """Handles promotion of a candidate to production (file-level, managed files only)."""

    # File classification from PRODUCTION_ASSET_DEPENDENCY.md
    # integration/ev_actuals_*.csv are deliberately absent: see C1_PROTECTED_FILES.
    EV_CHANGED = [
        "dashboard_comparison_dno_monthly.csv",
        "dashboard_comparison_lsoa.csv",
        "dashboard_comparison_la.csv",
        "dashboard_comparison_laep.csv",
        "integration/monthly_forecast_dno.csv",
        "integration/monthly_forecast_la.csv",
        "integration/monthly_forecast_laep.csv",
    ]

    SHARED_METADATA_CHANGED = ["integration/metadata.json"]

    COPIED_UNCHANGED = [
        "integration/hp_actuals_dno.csv",
        "integration/hp_actuals_la.csv",
        "integration/hp_actuals_laep.csv",
        "integration/hp_monthly_forecast_dno.csv",
        "integration/hp_monthly_forecast_la.csv",
        "integration/hp_monthly_forecast_laep.csv",
        "integration/hp_metadata.json",
    ]

    def __init__(self, project_root: str):
        self.project_root = project_root
        self.backup = ProductionBackup(project_root)
        self.validator = CandidateValidator(project_root)

    def promote(self, run_id: str) -> Tuple[bool, str]:
        """
        Promote a candidate: replace only the managed files, one at a time, after all checks pass.

        Returns: (success, message)
        """
        # Validate candidate
        can_promote, errors = self.validator.can_promote(run_id)
        if not can_promote:
            return False, f"Promotion blocked: {'; '.join(errors)}"

        # Get candidate metadata
        metadata = self.validator.get_candidate_metadata(run_id)
        if not metadata:
            return False, "Candidate metadata not found"

        old_vintage = metadata.get("old_vintage", "unknown")
        new_vintage = metadata.get("new_vintage", "unknown")

        try:
            with PromotionLock(self.backup.backup_root):
                return self._promote_locked(run_id, old_vintage, new_vintage)
        except PromotionBusy as e:
            return False, f"Promotion blocked: {e}"

    def _promote_locked(self, run_id: str, old_vintage: str, new_vintage: str) -> Tuple[bool, str]:
        pending = incomplete_operations(self.backup.backup_root)
        if pending:
            ids = [p["backup_id"] for p in pending]
            return False, f"Promotion blocked: incomplete operation(s) {ids}; recover with ManualRollback.rollback first"

        prod_output = os.path.join(self.project_root, "project", "output_processed")
        protected_before = _hashes(prod_output, C1_PROTECTED_FILES + tuple(self.COPIED_UNCHANGED))

        # Step 1: Create backup
        backup_success, backup_id = self.backup.create_backup(run_id, old_vintage, new_vintage)
        if not backup_success:
            return False, f"Backup failed: {backup_id}"
        backup_dir = os.path.join(self.backup.backup_root, backup_id)
        journal_path = os.path.join(backup_dir, JOURNAL)
        journal = {"operation": "promote", "run_id": run_id, "status": "PREPARING", "files": {}}
        replaced = []

        try:
            # Step 2: Stage the new versions of the managed files beside the backup
            staging_dir = os.path.join(self.project_root, "project", "dft_staging", f"run_{run_id}")
            candidate_src = os.path.join(staging_dir, "outputs")
            staged_root = os.path.join(backup_dir, "staged")
            plan = {}
            for file_path in self.EV_CHANGED:
                src = os.path.join(candidate_src, file_path)
                if os.path.exists(src):
                    dst = os.path.join(staged_root, file_path)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                    plan[file_path] = dst
            for file_path in self.SHARED_METADATA_CHANGED:      # vintage field only
                prod_meta = os.path.join(prod_output, file_path)
                if os.path.exists(prod_meta):
                    with open(prod_meta, 'r') as f:
                        meta = json.load(f)
                    meta["ev_vintage"] = new_vintage
                    dst = os.path.join(staged_root, file_path)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    with open(dst, 'w') as f:
                        json.dump(meta, f, indent=2)
                    plan[file_path] = dst

            # Step 3: Checks before anything in production is touched
            for file_path in FORECAST_FILES:
                if file_path in plan:
                    ok, msg = forecast_rows_preserved(os.path.join(prod_output, file_path), plan[file_path])
                    if not ok:
                        raise Exception(msg)
            if set(plan) - set(PROMOTION_MANAGED_FILES):
                raise Exception(f"unmanaged files in promotion plan: {sorted(set(plan) - set(PROMOTION_MANAGED_FILES))}")

            # Step 4: Replace the managed files one at a time (journalled)
            journal["files"] = {p: {"old": _sha256(os.path.join(prod_output, p)) if os.path.exists(os.path.join(prod_output, p)) else None,
                                    "new": _sha256(s), "state": "pending"} for p, s in plan.items()}
            journal["status"] = "IN_PROGRESS"
            _write_json_atomic(journal_path, journal)
            for file_path, staged in plan.items():
                _replace_file(staged, os.path.join(prod_output, file_path), journal["files"][file_path]["new"])
                replaced.append(file_path)
                journal["files"][file_path]["state"] = "replaced"
                _write_json_atomic(journal_path, journal)
                _after_replace(file_path)

            # Step 5: Post-promotion validation
            if _hashes(prod_output, C1_PROTECTED_FILES + tuple(self.COPIED_UNCHANGED)) != protected_before:
                raise Exception("C1 EV actuals or HP files changed during promotion")
            wrong = [p for p in plan if _sha256(os.path.join(prod_output, p)) != journal["files"][p]["new"]]
            if wrong:
                raise Exception(f"published files do not match the staged candidate: {wrong}")
            valid, msg = self._validate_production()
            if not valid:
                raise Exception(f"Post-promotion validation failed: {msg}")

            journal["status"] = "COMPLETE"
            _write_json_atomic(journal_path, journal)
            shutil.rmtree(staged_root, ignore_errors=True)

            # Step 6: Record in audit
            self._record_promotion(run_id, old_vintage, new_vintage, backup_id)

            return True, f"Promotion successful. Run {run_id} → Production. Backup: {backup_id}"

        except Exception as e:
            if not replaced:
                journal["status"] = "NOT_APPLIED"
                journal["error"] = str(e)
                _write_json_atomic(journal_path, journal)
                return False, f"Promotion failed: {str(e)}. Production was not changed."
            ok, restore_msg = self.backup.restore_backup(backup_id)
            journal["status"] = "ROLLED_BACK" if ok else "RECOVERY_REQUIRED"
            journal["error"] = str(e)
            journal["restore"] = restore_msg
            _write_json_atomic(journal_path, journal)
            return False, f"Promotion failed: {str(e)}. Rollback: {restore_msg}"

    def _compute_hash(self, file_path: str) -> str:
        """Compute SHA256 hash of file for byte-identity verification."""
        return _sha256(file_path)

    def _validate_production(self) -> Tuple[bool, str]:
        """Validate all 19 files present and readable after promotion."""
        prod_output = os.path.join(self.project_root, "project", "output_processed")

        # Check all required files exist
        all_files = self.EV_CHANGED + self.SHARED_METADATA_CHANGED + self.COPIED_UNCHANGED + list(C1_PROTECTED_FILES)
        missing = []
        for file_path in all_files:
            full_path = os.path.join(prod_output, file_path)
            if not os.path.exists(full_path):
                missing.append(file_path)

        if missing:
            return False, f"Missing files: {', '.join(missing)}"

        # Verify EV CSV files are parseable
        for file_path in self.EV_CHANGED:
            if file_path.endswith(".csv"):
                try:
                    full_path = os.path.join(prod_output, file_path)
                    pd.read_csv(full_path, nrows=10)
                except Exception as e:
                    return False, f"Cannot read {file_path}: {str(e)}"

        # Verify metadata.json is valid
        try:
            meta_path = os.path.join(prod_output, "integration", "metadata.json")
            with open(meta_path, 'r') as f:
                meta = json.load(f)
                if "ev_vintage" not in meta:
                    return False, "Metadata missing ev_vintage field"
        except Exception as e:
            return False, f"Metadata validation failed: {str(e)}"

        return True, "All 19 files validated"

    def _record_promotion(self, run_id: str, old_vintage: str, new_vintage: str, backup_id: str):
        """Record promotion in audit history."""
        history_path = os.path.join(self.project_root, "project", "promotion_history.jsonl")

        record = {
            "timestamp": datetime.now().isoformat(),
            "event": "PROMOTION",
            "run_id": run_id,
            "old_vintage": old_vintage,
            "new_vintage": new_vintage,
            "backup_id": backup_id,
            "result": "SUCCESS",
        }

        with open(history_path, 'a') as f:
            f.write(json.dumps(record) + "\n")


class ManualRollback:
    """Enables manual rollback to previous production state."""

    def __init__(self, project_root: str):
        self.project_root = project_root
        self.backup = ProductionBackup(project_root)

    def list_available_backups(self) -> List[Dict]:
        """List available backups for rollback."""
        backup_root = os.path.join(self.project_root, "project", "production_backups")
        backups = []

        if not os.path.exists(backup_root):
            return backups

        for backup_dir in sorted(os.listdir(backup_root), reverse=True):
            manifest_path = os.path.join(backup_root, backup_dir, "manifest.json")
            if os.path.exists(manifest_path):
                with open(manifest_path, 'r') as f:
                    manifest = json.load(f)
                    manifest["rollback_supported"] = manifest.get("format_version") == BACKUP_FORMAT_VERSION
                    journal = read_journal(os.path.join(backup_root, backup_dir))
                    manifest["operation_status"] = journal.get("status") if journal else None
                    backups.append(manifest)

        return backups

    def rollback(self, backup_id: str) -> Tuple[bool, str]:
        """Restore the managed files from a verified backup (also recovers an interrupted operation)."""
        # Refuse unsupported, incomplete or corrupt backups before touching anything
        ok, message, _ = self.backup.verify_backup(backup_id)
        if not ok:
            return False, f"Rollback refused: {message}"
        try:
            with PromotionLock(self.backup.backup_root):
                return self._rollback_locked(backup_id)
        except PromotionBusy as e:
            return False, f"Rollback refused: {e}"

    def _rollback_locked(self, backup_id: str) -> Tuple[bool, str]:
        backup_root = self.backup.backup_root
        others = [p for p in incomplete_operations(backup_root)
                  if p["backup_id"] != backup_id and p.get("target_backup_id") != backup_id]
        if others:
            return False, f"Rollback refused: other incomplete operation(s) {[p['backup_id'] for p in others]}"

        # First, backup current production
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        current_backup_id = f"rollback_{timestamp}_pre_restore"

        backup_success, pre_id = self.backup.create_backup(current_backup_id, "current", "restoring")
        if not backup_success:
            return False, "Failed to backup current state before rollback"
        pre_journal_path = os.path.join(backup_root, pre_id, JOURNAL)
        _write_json_atomic(pre_journal_path, {"operation": "rollback", "target_backup_id": backup_id, "status": "IN_PROGRESS"})

        # Restore previous
        restore_success, msg = self.backup.restore_backup(backup_id)
        if not restore_success:
            _write_json_atomic(pre_journal_path, {"operation": "rollback", "target_backup_id": backup_id,
                                                  "status": "RECOVERY_REQUIRED", "error": msg})
            return False, msg

        _write_json_atomic(pre_journal_path, {"operation": "rollback", "target_backup_id": backup_id, "status": "COMPLETE"})
        # Close any earlier interrupted journals this rollback has now resolved
        for p in incomplete_operations(backup_root):
            if p["backup_id"] == backup_id or p.get("target_backup_id") == backup_id:
                path = os.path.join(backup_root, p["backup_id"], JOURNAL)
                journal = read_journal(os.path.join(backup_root, p["backup_id"])) or {}
                journal.update(status="RECOVERED", recovered_by=pre_id)
                _write_json_atomic(path, journal)

        # Record rollback
        self._record_rollback(backup_id)

        return True, f"Rolled back to {backup_id}. Previous state backed up as {pre_id}"

    def _record_rollback(self, backup_id: str):
        """Record rollback in audit history."""
        history_path = os.path.join(self.project_root, "project", "promotion_history.jsonl")

        record = {
            "timestamp": datetime.now().isoformat(),
            "event": "ROLLBACK",
            "backup_id": backup_id,
            "result": "SUCCESS",
        }

        with open(history_path, 'a') as f:
            f.write(json.dumps(record) + "\n")
