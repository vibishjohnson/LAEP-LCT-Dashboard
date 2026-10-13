#!/usr/bin/env python3
"""
Phase 5: Production Promotion Tests

Coverage:
- Candidate eligibility validation (including quarantine and EV-actuals protection)
- Backup creation, verification and file-scoped restoration
- File-level promotion of managed files only
- DFES/NESO forecast protection; SMMT nowcast promotion
- Automatic rollback, interrupted operations and locking
- Manual rollback (verified backups only; historic backups refused)
- Synthetic data protection

All tests that write use disposable directories (tests/promotion_fixtures.py). Read-only eligibility checks against
the real project root create nothing.
"""

import os
import sys
import time
import unittest
import tempfile
import shutil
import json
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from data_manager import promotion as promotion_module  # noqa: E402
from data_manager.promotion import (  # noqa: E402
    AtomicPromotion, CandidateValidator, ProductionBackup, ManualRollback, PromotionLock,
    C1_PROTECTED_FILES, PROMOTION_MANAGED_FILES, candidate_status, incomplete_operations,
)
from tests import promotion_fixtures as fx  # noqa: E402


class TestCandidateEligibility(unittest.TestCase):
    """Tests for candidate eligibility gates (read-only against the real project root)."""

    def setUp(self):
        self.validator = CandidateValidator(PROJECT_ROOT)

    def test_1_valid_candidate_eligible(self):
        """Test: Valid candidate passes eligibility checks."""
        # A properly formed candidate would pass
        can_promote, errors = self.validator.can_promote("run_20260930_143022")
        # This specific run may not exist, which is expected
        self.assertIsInstance(can_promote, bool)

    def test_2_synthetic_candidate_rejected(self):
        """Test: Synthetic candidate rejected."""
        tmp = tempfile.mkdtemp()
        try:
            fx.make_project(tmp)
            fx.make_candidate(tmp, "run_SYNTHETIC_test")
            can_promote, errors = CandidateValidator(tmp).can_promote("run_SYNTHETIC_test")
            self.assertFalse(can_promote, "Synthetic candidate must be rejected")
            self.assertIn("synthetic", " ".join(errors).lower())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_3_test_candidate_rejected(self):
        """Test: Test candidate rejected."""
        tmp = tempfile.mkdtemp()
        try:
            fx.make_project(tmp)
            fx.make_candidate(tmp, "run_TEST_20260930")
            can_promote, errors = CandidateValidator(tmp).can_promote("run_TEST_20260930")
            self.assertFalse(can_promote, "Test candidate must be rejected")
            self.assertIn("test", " ".join(errors).lower())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_4_missing_candidate_rejected(self):
        """Test: Non-existent candidate rejected."""
        can_promote, errors = self.validator.can_promote("run_nonexistent_xyz")
        self.assertFalse(can_promote, "Missing candidate must be rejected")


class TestProductionBackup(unittest.TestCase):
    """Tests for backup creation and restoration."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.prod_output = fx.make_project(self.temp_dir)
        self.backup = ProductionBackup(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_5_backup_created(self):
        """Test: Backup created successfully."""
        success, backup_id = self.backup.create_backup("run_test", "2026-03-31", "2026-06-30")
        self.assertTrue(success, f"Backup creation failed: {backup_id}")
        self.assertIn("promotion_", backup_id)

    def test_6_backup_has_manifest(self):
        """Test: Backup manifest created with per-file hashes of the managed files only."""
        success, backup_id = self.backup.create_backup("run_test", "2026-03-31", "2026-06-30")

        manifest_path = os.path.join(self.backup.backup_root, backup_id, "manifest.json")
        self.assertTrue(os.path.exists(manifest_path))

        with open(manifest_path, 'r') as f:
            manifest = json.load(f)
            self.assertEqual(manifest['old_vintage'], "2026-03-31")
            self.assertEqual(manifest['new_vintage'], "2026-06-30")
            self.assertEqual(manifest['format_version'], 2)
            self.assertEqual(set(manifest['managed_files_sha256']), set(PROMOTION_MANAGED_FILES))
            self.assertFalse(set(manifest['managed_files_sha256']) & set(C1_PROTECTED_FILES))

    def test_7_backup_restored(self):
        """Test: Backup restores the managed files only; unrelated and C1 files are left alone."""
        success, backup_id = self.backup.create_backup("run_test", "2026-03-31", "2026-06-30")
        forecast = os.path.join(self.prod_output, "integration", "monthly_forecast_dno.csv")
        original = fx.sha(forecast)
        with open(forecast, "ab") as f:
            f.write(b"changed\r\n")
        os.remove(os.path.join(self.prod_output, "integration", "metadata.json"))
        unrelated = os.path.join(self.prod_output, "unrelated_report.csv")
        with open(unrelated, "wb") as f:
            f.write(b"edited after the backup\r\n")
        c1_before = {p: fx.sha(os.path.join(self.prod_output, p)) for p in C1_PROTECTED_FILES}

        restore_success, msg = self.backup.restore_backup(backup_id)
        self.assertTrue(restore_success, msg)
        self.assertEqual(fx.sha(forecast), original)
        self.assertTrue(os.path.exists(os.path.join(self.prod_output, "integration", "metadata.json")))
        with open(unrelated, "rb") as f:
            self.assertEqual(f.read(), b"edited after the backup\r\n")
        self.assertEqual({p: fx.sha(os.path.join(self.prod_output, p)) for p in C1_PROTECTED_FILES}, c1_before)


class TestAtomicPromotion(unittest.TestCase):
    """Tests for promotion logic."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.prod_output = fx.make_project(self.temp_dir)
        self.promoter = AtomicPromotion(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_8_non_existent_candidate_promotion_fails(self):
        """Test: Promotion fails for non-existent candidate."""
        success, msg = self.promoter.promote("run_nonexistent")
        self.assertFalse(success, "Non-existent candidate promotion must fail")

    def test_9_synthetic_promotion_blocked(self):
        """Test: Synthetic candidate promotion blocked."""
        fx.make_candidate(self.temp_dir, "run_SYNTHETIC_xyz", forecast=fx.forecast_frame(smmt=True))
        success, msg = self.promoter.promote("run_SYNTHETIC_xyz")
        self.assertFalse(success, "Synthetic promotion must be blocked")
        self.assertIn("synthetic", msg.lower())

    def test_10_no_backup_for_blocked_candidate(self):
        """Test: An ineligible candidate creates no backup and changes nothing."""
        before = fx.tree(self.temp_dir)
        self.promoter.promote("run_missing")
        self.assertEqual(fx.tree(self.temp_dir), before)


class TestC1SafePromotion(unittest.TestCase):
    """C1 safeguards: file-level promotion, forecast protection, EV actuals protection, rollback, interruption."""

    RUN = "smmt_nowcast_20261101_090000"

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="promo_")
        self.out = fx.make_project(self.root)
        self.integ = os.path.join(self.out, "integration")
        self.promoter = AtomicPromotion(self.root)

    def tearDown(self):
        promotion_module._after_replace = lambda rel_path: None
        shutil.rmtree(self.root, ignore_errors=True)

    def outputs(self):
        return fx.tree(self.out)

    def assert_rejected_without_change(self, candidate_forecast, expect):
        fx.make_candidate(self.root, self.RUN, forecast=candidate_forecast)
        before = self.outputs()
        ok, msg = self.promoter.promote(self.RUN)
        self.assertFalse(ok)
        self.assertIn(expect, msg)
        self.assertEqual(self.outputs(), before)

    def test_valid_nowcast_only_candidate_is_promoted(self):
        fx.make_candidate(self.root, self.RUN, forecast=fx.forecast_frame(smmt=True))
        before = self.outputs()
        unrelated = os.path.join(self.out, "unrelated_report.csv")
        inode_before = os.stat(unrelated).st_ino
        ok, msg = self.promoter.promote(self.RUN)
        self.assertTrue(ok, msg)
        after = self.outputs()
        changed = {k for k in after if after[k] != before.get(k)}
        self.assertIn("integration/monthly_forecast_dno.csv", changed)
        self.assertTrue(changed <= set(PROMOTION_MANAGED_FILES))
        self.assertEqual(os.stat(unrelated).st_ino, inode_before)            # not copied or moved
        df = pd.read_csv(os.path.join(self.integ, "monthly_forecast_dno.csv"))
        self.assertEqual(int((df.forecast_source == "SMMT_NOWCAST").sum()), 12)
        self.assertFalse(os.path.exists(self.out + "_candidate") or os.path.exists(self.out + "_old"))

    def test_missing_dfes_neso_rows_rejected(self):
        f = fx.forecast_frame(smmt=True)
        self.assert_rejected_without_change(f[~((f.forecast_source == "NESO") & (f.period == "2025-05") & (f.DNO == "LPN"))],
                                            "DFES/NESO forecast rows differ")

    def test_duplicate_forecast_keys_rejected(self):
        f = fx.forecast_frame(smmt=True)
        self.assert_rejected_without_change(pd.concat([f, f[f.forecast_source == "DFES"].head(1)]), "differ")
        f2 = fx.forecast_frame(smmt=True)
        self.assert_rejected_without_change(pd.concat([f2, f2[f2.forecast_source == "SMMT_NOWCAST"].head(1)]),
                                            "duplicate SMMT_NOWCAST")

    def test_modified_dfes_neso_value_rejected(self):
        f = fx.forecast_frame(smmt=True)
        f.loc[f.index[(f.forecast_source == "DFES")][3], "value"] += 1
        self.assert_rejected_without_change(f, "values differ")

    def test_malformed_forecast_rejected(self):
        f = fx.forecast_frame(smmt=True).drop(columns=["forecast_scenario"])
        self.assert_rejected_without_change(f, "columns differ")

    def test_ev_actuals_cannot_be_overwritten(self):
        fx.make_candidate(self.root, self.RUN, forecast=fx.forecast_frame(smmt=True), with_ev_actuals=True)
        before = self.outputs()
        ok, msg = self.promoter.promote(self.RUN)
        self.assertFalse(ok)
        self.assertIn("EV actuals are published only by the C1 release tool", msg)
        self.assertEqual(self.outputs(), before)

    def test_ev_actuals_blocked_even_if_validator_and_file_list_are_bypassed(self):
        fx.make_candidate(self.root, self.RUN, forecast=fx.forecast_frame(smmt=True), with_ev_actuals=True)
        original_validator = CandidateValidator.can_promote
        original_list = AtomicPromotion.EV_CHANGED
        try:
            CandidateValidator.can_promote = lambda self, run_id: (True, [])
            AtomicPromotion.EV_CHANGED = original_list + list(C1_PROTECTED_FILES)
            before = self.outputs()
            ok, msg = AtomicPromotion(self.root).promote(self.RUN)
        finally:
            CandidateValidator.can_promote = original_validator
            AtomicPromotion.EV_CHANGED = original_list
        self.assertFalse(ok)
        self.assertIn("unmanaged files", msg)
        self.assertEqual(self.outputs(), before)

    def test_quarantined_candidate_cannot_be_promoted(self):
        fx.make_candidate(self.root, self.RUN, forecast=fx.forecast_frame(smmt=True), quarantined=True)
        before = self.outputs()
        ok, msg = self.promoter.promote(self.RUN)
        self.assertFalse(ok)
        self.assertIn("quarantined", msg)
        self.assertEqual(self.outputs(), before)
        status = candidate_status(self.root, self.RUN)
        self.assertEqual(status["status"], "INELIGIBLE — QUARANTINED")
        self.assertFalse(status["can_promote"])

    def test_candidate_status_for_eligible_and_other_ineligible_runs(self):
        fx.make_candidate(self.root, self.RUN, forecast=fx.forecast_frame(smmt=True))
        self.assertEqual(candidate_status(self.root, self.RUN), {"status": "READY FOR PROMOTION", "can_promote": True, "errors": []})
        fx.make_candidate(self.root, "smmt_other", forecast=fx.forecast_frame(smmt=True), with_ev_actuals=True)
        self.assertEqual(candidate_status(self.root, "smmt_other")["status"], "INELIGIBLE")

    def test_file_scoped_rollback_preserves_unrelated_files(self):
        fx.make_candidate(self.root, self.RUN, forecast=fx.forecast_frame(smmt=True))
        original_forecast = fx.sha(os.path.join(self.integ, "monthly_forecast_dno.csv"))
        ok, msg = self.promoter.promote(self.RUN)
        self.assertTrue(ok, msg)
        backup_id = msg.split("Backup: ")[1]
        with open(os.path.join(self.out, "created_after_backup.csv"), "wb") as f:
            f.write(b"new\r\n")
        with open(os.path.join(self.integ, "ev_actuals_dno.csv"), "wb") as f:   # a later C1 release
            f.write(b"period,EV_Type,value\r\n2026-06,BEV,9\r\n")
        c1_now = {p: fx.sha(os.path.join(self.out, p)) for p in C1_PROTECTED_FILES}
        ok, msg = ManualRollback(self.root).rollback(backup_id)
        self.assertTrue(ok, msg)
        self.assertEqual(fx.sha(os.path.join(self.integ, "monthly_forecast_dno.csv")), original_forecast)
        self.assertTrue(os.path.exists(os.path.join(self.out, "created_after_backup.csv")))
        self.assertEqual({p: fx.sha(os.path.join(self.out, p)) for p in C1_PROTECTED_FILES}, c1_now)
        self.assertTrue(os.path.exists(os.path.join(self.out, "unrelated_report.csv")))

    def test_unsupported_historic_backup_refused(self):
        bdir = os.path.join(self.root, "project", "production_backups", "promotion_20261001_124032_old")
        os.makedirs(os.path.join(bdir, "output_processed", "integration"))
        with open(os.path.join(bdir, "manifest.json"), "w") as f:
            json.dump({"backup_id": "promotion_20261001_124032_old", "files_backed_up": ["integration/ev_actuals_dno.csv"]}, f)
        shutil.copyfile(os.path.join(self.integ, "monthly_forecast_dno.csv"),
                        os.path.join(bdir, "output_processed", "integration", "monthly_forecast_dno.csv"))
        before = fx.tree(self.root)
        ok, msg = ManualRollback(self.root).rollback("promotion_20261001_124032_old")
        self.assertFalse(ok)
        self.assertIn("historic format", msg)
        self.assertEqual(fx.tree(self.root), before)

    def test_backup_corruption_detected_before_restoration(self):
        fx.make_candidate(self.root, self.RUN, forecast=fx.forecast_frame(smmt=True))
        ok, msg = self.promoter.promote(self.RUN)
        backup_id = msg.split("Backup: ")[1]
        damaged = os.path.join(self.root, "project", "production_backups", backup_id, "output_processed",
                               "integration", "monthly_forecast_dno.csv")
        with open(damaged, "ab") as f:
            f.write(b"x")
        before = fx.tree(self.root)
        ok, msg = ManualRollback(self.root).rollback(backup_id)
        self.assertFalse(ok)
        self.assertIn("corrupt", msg)
        self.assertEqual(fx.tree(self.root), before)

    def test_failure_after_first_replacement_is_rolled_back_automatically(self):
        fx.make_candidate(self.root, self.RUN, forecast=fx.forecast_frame(smmt=True))
        before = self.outputs()

        def boom(rel_path):
            raise RuntimeError("injected failure")
        promotion_module._after_replace = boom
        ok, msg = self.promoter.promote(self.RUN)
        self.assertFalse(ok)
        self.assertIn("Rollback: Restored", msg)
        self.assertEqual(self.outputs(), before)
        self.assertEqual(incomplete_operations(os.path.join(self.root, "project", "production_backups")), [])

    def test_hard_interrupted_promotion_blocks_and_is_recoverable(self):
        fx.make_candidate(self.root, self.RUN, forecast=fx.forecast_frame(smmt=True))
        before = self.outputs()
        r = fx.run_python(
            "import os\nfrom data_manager import promotion as p\n"
            "p._after_replace = lambda rel: os._exit(9)\n"
            f"p.AtomicPromotion({self.root!r}).promote({self.RUN!r})\n", cwd=self.root)
        self.assertEqual(r.returncode, 9, r.stderr)
        backup_root = os.path.join(self.root, "project", "production_backups")
        pending = incomplete_operations(backup_root)
        self.assertEqual(len(pending), 1)
        self.assertNotEqual(self.outputs(), before)                          # one file was replaced
        ok, msg = AtomicPromotion(self.root).promote(self.RUN)               # lock released by the OS; journal blocks
        self.assertFalse(ok)
        self.assertIn("incomplete operation", msg)
        ok, msg = ManualRollback(self.root).rollback(pending[0]["backup_id"])
        self.assertTrue(ok, msg)
        self.assertEqual(self.outputs(), before)
        self.assertEqual(incomplete_operations(backup_root), [])
        ok, msg = AtomicPromotion(self.root).promote(self.RUN)               # recoverable: promotion works again
        self.assertTrue(ok, msg)

    def test_hard_interrupted_rollback_is_recoverable(self):
        fx.make_candidate(self.root, self.RUN, forecast=fx.forecast_frame(smmt=True))
        before = self.outputs()
        ok, msg = self.promoter.promote(self.RUN)
        backup_id = msg.split("Backup: ")[1]
        r = fx.run_python(
            "import os\nfrom data_manager import promotion as p\n"
            "real = p._replace_file\n"
            "calls = []\n"
            "def first_then_die(src, dst, expected):\n"
            "    real(src, dst, expected); calls.append(dst)\n"
            "    if len(calls) == 2: os._exit(9)\n"
            "p._replace_file = first_then_die\n"
            f"p.ManualRollback({self.root!r}).rollback({backup_id!r})\n", cwd=self.root)
        self.assertEqual(r.returncode, 9, r.stderr)
        backup_root = os.path.join(self.root, "project", "production_backups")
        self.assertTrue(incomplete_operations(backup_root))
        ok, msg = AtomicPromotion(self.root).promote(self.RUN)
        self.assertFalse(ok)
        ok, msg = ManualRollback(self.root).rollback(backup_id)              # re-run completes the rollback
        self.assertTrue(ok, msg)
        self.assertEqual(self.outputs(), before)
        self.assertEqual(incomplete_operations(backup_root), [])

    def test_concurrent_operation_is_refused(self):
        fx.make_candidate(self.root, self.RUN, forecast=fx.forecast_frame(smmt=True))
        backup_root = os.path.join(self.root, "project", "production_backups")
        before = self.outputs()
        with PromotionLock(backup_root):
            ok, msg = AtomicPromotion(self.root).promote(self.RUN)
            self.assertFalse(ok)
            self.assertIn("in progress", msg)
        self.assertEqual(self.outputs(), before)

    def test_lock_held_by_another_process(self):
        import subprocess
        fx.make_candidate(self.root, self.RUN, forecast=fx.forecast_frame(smmt=True))
        backup_root = os.path.join(self.root, "project", "production_backups")
        marker = os.path.join(self.root, "held")
        code = (f"import sys, time; sys.path.insert(0, {PROJECT_ROOT!r})\n"
                f"from data_manager.promotion import PromotionLock\n"
                f"l = PromotionLock({backup_root!r}); l.__enter__(); open({marker!r}, 'w').close(); time.sleep(30)\n")
        proc = subprocess.Popen([sys.executable, "-c", code], env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        try:
            for _ in range(300):
                if os.path.exists(marker):
                    break
                time.sleep(0.1)
            before = self.outputs()
            ok, msg = AtomicPromotion(self.root).promote(self.RUN)
            self.assertFalse(ok)
            self.assertIn("in progress", msg)
            ok, msg = ManualRollback(self.root).rollback("promotion_missing")
            self.assertFalse(ok)
            self.assertEqual(self.outputs(), before)
        finally:
            proc.kill()
            proc.wait()
        ok, msg = AtomicPromotion(self.root).promote(self.RUN)               # released when the holder dies
        self.assertTrue(ok, msg)


class TestManualRollback(unittest.TestCase):
    """Tests for manual rollback capability."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.rollback = ManualRollback(self.temp_dir)
        self.backup = ProductionBackup(self.temp_dir)

        # Create mock backup
        self.backup_root = os.path.join(self.temp_dir, "project", "production_backups")
        os.makedirs(self.backup_root, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_11_list_available_backups(self):
        """Test: List available backups."""
        backups = self.rollback.list_available_backups()
        self.assertIsInstance(backups, list)

    def test_12_rollback_creates_audit_entry(self):
        """Test: Rollback creates audit history entry."""
        fx.make_project(self.temp_dir)
        success, backup_id = self.backup.create_backup("run_x", "2026-03-31", "2026-06-30")
        self.assertTrue(success, backup_id)
        ok, msg = self.rollback.rollback(backup_id)
        self.assertTrue(ok, msg)
        with open(os.path.join(self.temp_dir, "project", "promotion_history.jsonl")) as f:
            records = [json.loads(line) for line in f]
        self.assertEqual(records[-1]["event"], "ROLLBACK")
        self.assertEqual(records[-1]["backup_id"], backup_id)


class TestSyntheticDataProtection(unittest.TestCase):
    """Tests for synthetic data protection."""

    def test_13_synthetic_marker_blocks_promotion(self):
        """Test: Synthetic marker prevents promotion."""
        validator = CandidateValidator(PROJECT_ROOT)

        synthetic_runs = [
            "run_SYNTHETIC_q2",
            "run_TEST_fixture",
            "run_test_2026",
            "run_SYNTHETIC_data"
        ]

        for run_id in synthetic_runs:
            can_promote, errors = validator.can_promote(run_id)
            self.assertFalse(can_promote, f"{run_id} should be rejected")


class TestAuditHistory(unittest.TestCase):
    """Tests for audit history recording."""

    def test_15_promotion_recorded(self):
        """Test: Promotion events recorded in history."""
        tmp = tempfile.mkdtemp()
        try:
            fx.make_project(tmp)
            fx.make_candidate(tmp, "smmt_audit", forecast=fx.forecast_frame(smmt=True))
            ok, msg = AtomicPromotion(tmp).promote("smmt_audit")
            self.assertTrue(ok, msg)
            with open(os.path.join(tmp, "project", "promotion_history.jsonl")) as f:
                record = json.loads(f.readlines()[-1])
            self.assertEqual((record["event"], record["run_id"], record["result"]), ("PROMOTION", "smmt_audit", "SUCCESS"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
