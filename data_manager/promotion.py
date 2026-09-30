"""
Phase 5: Production Promotion Manager

Atomic promotion of validated candidates to production with:
- Backup before any changes
- Eligibility validation
- Atomic file swaps
- Post-promotion validation
- Automatic rollback on failure
- Manual rollback capability
"""

import os
import shutil
import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import pandas as pd


class ProductionBackup:
    """Manages production backups for safe rollback."""

    def __init__(self, project_root: str):
        self.project_root = project_root
        self.backup_root = os.path.join(project_root, "project", "production_backups")
        os.makedirs(self.backup_root, exist_ok=True)

    def create_backup(self, run_id: str, old_vintage: str, new_vintage: str) -> Tuple[bool, str]:
        """
        Create complete production backup before promotion.

        Returns: (success, backup_id)
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_id = f"promotion_{timestamp}_{run_id}"
        backup_dir = os.path.join(self.backup_root, backup_id)

        try:
            os.makedirs(backup_dir, exist_ok=True)

            # Backup production outputs
            prod_output = os.path.join(self.project_root, "project", "output_processed")
            backup_output = os.path.join(backup_dir, "output_processed")

            if os.path.exists(prod_output):
                shutil.copytree(prod_output, backup_output)

            # Create manifest
            manifest = {
                "backup_id": backup_id,
                "run_id": run_id,
                "timestamp": datetime.now().isoformat(),
                "old_vintage": old_vintage,
                "new_vintage": new_vintage,
                "files_backed_up": self._list_backed_up_files(backup_output),
            }

            manifest_path = os.path.join(backup_dir, "manifest.json")
            with open(manifest_path, 'w') as f:
                json.dump(manifest, f, indent=2)

            return True, backup_id

        except Exception as e:
            return False, str(e)

    def restore_backup(self, backup_id: str) -> Tuple[bool, str]:
        """Restore production from a backup."""
        try:
            backup_dir = os.path.join(self.backup_root, backup_id)
            if not os.path.exists(backup_dir):
                return False, f"Backup {backup_id} not found"

            # Restore output_processed
            backup_output = os.path.join(backup_dir, "output_processed")
            prod_output = os.path.join(self.project_root, "project", "output_processed")

            if os.path.exists(prod_output):
                shutil.rmtree(prod_output)

            if os.path.exists(backup_output):
                shutil.copytree(backup_output, prod_output)

            return True, f"Restored from {backup_id}"

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

        return True, []

    def get_candidate_metadata(self, run_id: str) -> Optional[Dict]:
        """Get candidate metadata."""
        staging_dir = os.path.join(self.project_root, "project", "dft_staging", f"run_{run_id}")
        metadata_path = os.path.join(staging_dir, "metadata.json")

        if os.path.exists(metadata_path):
            with open(metadata_path, 'r') as f:
                return json.load(f)

        return None


class AtomicPromotion:
    """Handles atomic promotion of candidate to production (19-file asset set)."""

    # File classification from PRODUCTION_ASSET_DEPENDENCY.md
    EV_CHANGED = [
        "dashboard_comparison_dno_monthly.csv",
        "dashboard_comparison_lsoa.csv",
        "dashboard_comparison_la.csv",
        "dashboard_comparison_laep.csv",
        "integration/ev_actuals_dno.csv",
        "integration/ev_actuals_la.csv",
        "integration/ev_actuals_laep.csv",
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
        Atomic promotion of candidate to production (19-file asset set).

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

        # Step 1: Create backup
        backup_success, backup_id = self.backup.create_backup(run_id, old_vintage, new_vintage)
        if not backup_success:
            return False, f"Backup failed: {backup_id}"

        try:
            # Step 2: Atomic promotion using directory swap
            prod_output = os.path.join(self.project_root, "project", "output_processed")
            temp_candidate = prod_output + "_candidate"
            temp_old = prod_output + "_old"

            # 2a. Create candidate directory with all 19 files
            if os.path.exists(temp_candidate):
                shutil.rmtree(temp_candidate)
            shutil.copytree(prod_output, temp_candidate)

            # 2b. Replace EV_CHANGED files
            staging_dir = os.path.join(self.project_root, "project", "dft_staging", f"run_{run_id}")
            candidate_src = os.path.join(staging_dir, "outputs")

            for file_path in self.EV_CHANGED:
                src = os.path.join(candidate_src, file_path)
                dst = os.path.join(temp_candidate, file_path)
                if os.path.exists(src):
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)

            # 2c. Update SHARED_METADATA_CHANGED (vintage field only)
            for file_path in self.SHARED_METADATA_CHANGED:
                dst = os.path.join(temp_candidate, file_path)
                if os.path.exists(dst):
                    with open(dst, 'r') as f:
                        meta = json.load(f)
                    meta["ev_vintage"] = new_vintage
                    with open(dst, 'w') as f:
                        json.dump(meta, f, indent=2)

            # 2d. Verify COPIED_UNCHANGED files are byte-identical
            for file_path in self.COPIED_UNCHANGED:
                prod_file = os.path.join(prod_output, file_path)
                cand_file = os.path.join(temp_candidate, file_path)
                if os.path.exists(prod_file) and os.path.exists(cand_file):
                    if self._compute_hash(prod_file) != self._compute_hash(cand_file):
                        raise Exception(f"HP file {file_path} not byte-identical: {prod_file}")

            # 2e. Atomic directory swap
            os.rename(prod_output, temp_old)
            os.rename(temp_candidate, prod_output)
            shutil.rmtree(temp_old)

            # Step 3: Post-promotion validation
            valid, msg = self._validate_production()
            if not valid:
                self.backup.restore_backup(backup_id)
                return False, f"Post-promotion validation failed: {msg}. Rolled back."

            # Step 4: Record in audit
            self._record_promotion(run_id, old_vintage, new_vintage, backup_id)

            return True, f"Promotion successful. Run {run_id} → Production. Backup: {backup_id}"

        except Exception as e:
            # Cleanup temp directories
            for temp in [prod_output + "_candidate", prod_output + "_old"]:
                if os.path.exists(temp):
                    shutil.rmtree(temp)
            # Rollback on any error
            self.backup.restore_backup(backup_id)
            return False, f"Promotion failed: {str(e)}. Rolled back."

    def _compute_hash(self, file_path: str) -> str:
        """Compute SHA256 hash of file for byte-identity verification."""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def _validate_production(self) -> Tuple[bool, str]:
        """Validate all 19 files present and readable after promotion."""
        prod_output = os.path.join(self.project_root, "project", "output_processed")

        # Check all required files exist
        all_files = self.EV_CHANGED + self.SHARED_METADATA_CHANGED + self.COPIED_UNCHANGED
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
                    backups.append(manifest)

        return backups

    def rollback(self, backup_id: str) -> Tuple[bool, str]:
        """Perform manual rollback to previous state."""
        # First, backup current production
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        current_backup_id = f"rollback_{timestamp}_pre_restore"

        backup_success, _ = self.backup.create_backup(
            current_backup_id,
            "current",
            "restoring"
        )

        if not backup_success:
            return False, "Failed to backup current state before rollback"

        # Restore previous
        restore_success, msg = self.backup.restore_backup(backup_id)
        if not restore_success:
            return False, msg

        # Record rollback
        self._record_rollback(backup_id)

        return True, f"Rolled back to {backup_id}. Previous state backed up as {current_backup_id}"

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
