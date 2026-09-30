#!/usr/bin/env python3
"""
Phase 1 Foundation Tests

Tests:
  - Database initialization and schema
  - SMMT CRUD operations
  - DfT version tracking
  - Processing history
  - DfT vintage detection
  - Flask application basics
"""

import os
import sys
import unittest
import tempfile
import shutil
from datetime import datetime

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, PROJECT_ROOT)

from data_manager.db import Database
from data_manager.dft_inspector import DftInspector
from app_data_manager import app


class TestDatabase(unittest.TestCase):
    """Test database operations."""

    def setUp(self):
        """Create temporary database for testing."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.db = Database(self.db_path)

    def tearDown(self):
        """Clean up temporary database."""
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_database_initialization(self):
        """Test database creates schema."""
        self.assertTrue(os.path.exists(self.db_path))

    def test_smmt_add_entry(self):
        """Test adding SMMT entry."""
        entry_id = self.db.add_smmt_entry(
            year=2026,
            month=10,
            bev_cars=1234,
            bev_vans=567,
            phev_cars=891,
            phev_vans=234,
            entered_by="test_user",
            notes="Test entry"
        )
        self.assertIsNotNone(entry_id)
        self.assertGreater(entry_id, 0)

    def test_smmt_get_entries(self):
        """Test retrieving SMMT entries."""
        self.db.add_smmt_entry(2026, 10, 1000, 500, 800, 200)
        self.db.add_smmt_entry(2026, 11, 1100, 550, 900, 220)

        entries = self.db.get_smmt_entries()
        self.assertEqual(len(entries), 2)

    def test_smmt_unique_month_constraint(self):
        """Test that versioning preserves history (Phase 2)."""
        # Add original entry
        orig_id = self.db.add_smmt_entry(2026, 10, 1000, 500, 800, 200)
        current = self.db.get_current_smmt_entry(2026, 10)
        self.assertIsNotNone(current)
        self.assertEqual(current["bev_cars"], 1000)
        # Correct via versioning
        new_id = self.db.correct_smmt_entry(2026, 10, 2000, 1000, 1600, 400)
        self.assertNotEqual(new_id, orig_id)
        # All entries (2 rows: old + new)
        all_entries = self.db.get_smmt_entries(year=2026, month=10)
        self.assertEqual(len(all_entries), 2)
        # Only new version is current
        current_entry = self.db.get_current_smmt_entry(2026, 10)
        self.assertEqual(current_entry["bev_cars"], 2000)

    def test_smmt_update_status(self):
        """Test updating SMMT status."""
        self.db.add_smmt_entry(2026, 10, 1000, 500, 800, 200)
        self.db.update_smmt_status(2026, 10, "SUPERSEDED BY DfT")

        entries = self.db.get_smmt_entries(year=2026, month=10)
        self.assertEqual(entries[0]["status"], "SUPERSEDED BY DfT")

    def test_dft_version_add(self):
        """Test adding DfT version record."""
        version_id = self.db.add_dft_version(
            dataset_name="VEH0135",
            period_end="2026-09-30",
            file_path="/path/to/veh0135.csv",
            uploaded_by="test_user",
            validation_status="PASS"
        )
        self.assertIsNotNone(version_id)
        self.assertGreater(version_id, 0)

    def test_dft_version_get(self):
        """Test retrieving DfT versions."""
        self.db.add_dft_version("VEH0135", "2026-09-30", "/path/to/veh0135.csv", validation_status="PASS")
        self.db.add_dft_version("VEH0142", "2026-09-30", "/path/to/veh0142.csv", validation_status="PASS")

        versions = self.db.get_dft_versions()
        self.assertEqual(len(versions), 2)

    def test_dft_latest_version(self):
        """Test getting latest DfT version."""
        self.db.add_dft_version("VEH0135", "2026-06-30", "/path/to/veh0135_q2.csv", validation_status="PASS")
        self.db.add_dft_version("VEH0135", "2026-09-30", "/path/to/veh0135_q3.csv", validation_status="PASS")

        latest = self.db.get_latest_dft_version("VEH0135", production_only=False)
        self.assertEqual(latest["period_end"], "2026-09-30")

    def test_processing_history(self):
        """Test recording processing operations."""
        record_id = self.db.add_processing_record(
            operation="DfT_UPLOADED",
            status="SUCCESS",
            input_summary={"files": ["VEH0135", "VEH0142"]},
            output_summary={"records_processed": 1000}
        )
        self.assertIsNotNone(record_id)

        history = self.db.get_processing_history(limit=10)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["operation"], "DfT_UPLOADED")


class TestDftInspector(unittest.TestCase):
    """Test DfT inspection."""

    def test_inspector_initialization(self):
        """Test inspector creates without error."""
        inspector = DftInspector(PROJECT_ROOT)
        self.assertIsNotNone(inspector)

    def test_dft_table_detection(self):
        """Test detecting which DfT tables exist."""
        inspector = DftInspector(PROJECT_ROOT)
        tables = inspector.get_dft_tables()

        # Should have all 4 tables (or at least return dict with all keys)
        self.assertIn("VEH0135", tables)
        self.assertIn("VEH0125", tables)
        self.assertIn("VEH0105", tables)
        self.assertIn("VEH0142", tables)

    def test_dft_quarter_detection(self):
        """Test detecting latest DfT quarter."""
        inspector = DftInspector(PROJECT_ROOT)
        latest_quarter = inspector.detect_latest_dft_quarter()

        # Should detect a quarter (format: YYYY-MM-DD)
        if latest_quarter:
            parts = latest_quarter.split("-")
            self.assertEqual(len(parts), 3)
            self.assertEqual(len(parts[0]), 4)  # Year

    def test_current_ev_state(self):
        """Test getting current EV state."""
        inspector = DftInspector(PROJECT_ROOT)
        state = inspector.get_current_ev_state()

        self.assertIn("latest_dft_quarter", state)
        self.assertIn("dft_tables_present", state)
        self.assertIn("ev_outputs_exist", state)
        self.assertIn("latest_ev_period", state)

    def test_inspection_report(self):
        """Test generating inspection report."""
        inspector = DftInspector(PROJECT_ROOT)
        report = inspector.get_inspection_report()

        # Should be a non-empty string
        self.assertIsInstance(report, str)
        self.assertGreater(len(report), 0)
        self.assertIn("DfT", report)


class TestFlaskApp(unittest.TestCase):
    """Test Flask application."""

    def setUp(self):
        """Set up Flask test client."""
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_app_health(self):
        """Test health check endpoint."""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "ok")

    def test_app_index(self):
        """Test main page loads."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"LCT Data Manager", response.data)

    def test_api_dft_status(self):
        """Test DfT status API."""
        response = self.client.get("/api/dft-status")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        self.assertIn("latest_dft_quarter", data)
        self.assertIn("dft_tables", data)
        self.assertIn("ev_outputs", data)

    def test_api_processing_history(self):
        """Test processing history API."""
        response = self.client.get("/api/processing-history")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()

        # Should be a list (initially empty)
        self.assertIsInstance(data, list)


class TestPhase1Integrity(unittest.TestCase):
    """Test that Phase 1 doesn't modify production data."""

    def test_production_outputs_untouched(self):
        """Verify production EV outputs still exist and are readable."""
        project_dir = os.path.dirname(os.path.dirname(__file__))
        output_dir = os.path.join(project_dir, "project", "output_processed")

        key_files = [
            "dashboard_data_dno.csv",
            "dashboard_data_lsoa.csv",
            "ev_actuals_dno.csv",
        ]

        for filename in key_files:
            filepath = os.path.join(output_dir, filename)
            # File should exist
            self.assertTrue(
                os.path.exists(filepath),
                f"Production file missing: {filename}"
            )

    def test_dft_source_untouched(self):
        """Verify DfT source files untouched."""
        project_dir = os.path.dirname(os.path.dirname(__file__))
        ev_dir = os.path.join(project_dir, "ev")

        key_files = ["df_VEH0135.csv", "df_VEH0125.csv", "VEH0105.csv", "VEH0142.csv"]

        for filename in key_files:
            filepath = os.path.join(ev_dir, filename)
            if os.path.exists(filepath):
                # File should be readable and have content
                import pandas as pd
                try:
                    df = pd.read_csv(filepath, nrows=1)
                    self.assertGreater(len(df.columns), 0)
                except Exception as e:
                    self.fail(f"Cannot read {filename}: {e}")


class TestSMMT(unittest.TestCase):
    """Test SMMT management."""

    def setUp(self):
        from data_manager.smmt import SMmtManager
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_smmt.db")
        self.smmt = SMmtManager(db_path=self.db_path)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_validate_valid_entry(self):
        is_valid, error = self.smmt.validate_smmt_input(2026, 10, 1000, 500, 800, 200)
        self.assertTrue(is_valid)
        self.assertIsNone(error)

    def test_validate_negative_value(self):
        is_valid, error = self.smmt.validate_smmt_input(2026, 10, -1000, 500, 800, 200)
        self.assertFalse(is_valid)
        self.assertIn("negative", error.lower())

    def test_validate_invalid_month(self):
        is_valid, error = self.smmt.validate_smmt_input(2026, 13, 1000, 500, 800, 200)
        self.assertFalse(is_valid)

    def test_validate_invalid_year(self):
        is_valid, error = self.smmt.validate_smmt_input(1990, 10, 1000, 500, 800, 200)
        self.assertFalse(is_valid)

    def test_add_smmt_entry(self):
        success, message = self.smmt.add_smmt_entry(2026, 10, 1000, 500, 800, 200)
        self.assertTrue(success)

        # Verify in database
        entry = self.smmt.db.get_current_smmt_entry(2026, 10)
        self.assertIsNotNone(entry)
        self.assertEqual(entry['bev_cars'], 1000)

    def test_duplicate_prevention(self):
        self.smmt.add_smmt_entry(2026, 11, 1000, 500, 800, 200)
        success, message = self.smmt.add_smmt_entry(2026, 11, 2000, 1000, 1600, 400)
        self.assertFalse(success)
        self.assertIn("exists", message.lower())

    def test_correct_entry_preserves_history(self):
        self.smmt.add_smmt_entry(2026, 12, 1000, 500, 800, 200)
        self.smmt.correct_smmt_entry(2026, 12, 1500, 700, 1000, 250)

        # Check that current entry is updated
        current = self.smmt.db.get_current_smmt_entry(2026, 12)
        self.assertEqual(current['bev_cars'], 1500)

        # Check that history has both versions
        all_versions = self.smmt.db.get_smmt_entry_history(2026, 12)
        self.assertEqual(len(all_versions), 2)

    def test_format_value(self):
        formatted = self.smmt.format_value(1234567)
        self.assertEqual(formatted, "1,234,567")

    def test_parse_value(self):
        parsed = self.smmt.parse_value("1,234,567")
        self.assertEqual(parsed, 1234567)

    def test_suggest_next_month(self):
        suggested = self.smmt.suggest_next_month()
        # Should suggest a month (exact value depends on DfT state)
        if suggested:
            year, month = suggested
            self.assertGreaterEqual(month, 1)
            self.assertLessEqual(month, 12)


class TestSMMTPhase2Integration(unittest.TestCase):
    """Integration tests for Phase 2."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test_p2.db")
        self.db = Database(self.db_path)

    def tearDown(self):
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_smmt_workflow(self):
        """Test complete SMMT workflow."""
        # Add first entry
        self.db.add_smmt_entry(2026, 10, 1000, 500, 800, 200)

        # Correct it
        self.db.correct_smmt_entry(2026, 10, 1200, 600, 900, 250)

        # Verify only one current entry
        current = self.db.get_current_smmt_entries_for_history()
        months_with_data = [e for e in current if e['year'] == 2026 and e['month'] == 10]
        self.assertEqual(len(months_with_data), 1)

        # Verify history preserved
        all_versions = self.db.get_smmt_entry_history(2026, 10)
        self.assertEqual(len(all_versions), 2)

    def test_database_versioning_schema(self):
        """Verify database schema supports versioning."""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(smmt_entries)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}

        # Check new columns exist
        self.assertIn('is_current', columns)
        self.assertIn('corrected_from_id', columns)
        self.assertIn('corrected_at', columns)
        conn.close()


if __name__ == "__main__":
    # Run tests
    unittest.main(verbosity=2)
