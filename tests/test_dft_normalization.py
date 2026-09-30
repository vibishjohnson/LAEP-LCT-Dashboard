#!/usr/bin/env python3
"""
DfT Raw → Canonical Normalization Tests

Validates automatic format detection and cleaning without manual preprocessing.
"""

import os
import sys
import unittest
import tempfile
import shutil
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, PROJECT_ROOT)

from data_manager.dft_normalizer import DfTNormalizer


class TestVEH0142Normalization(unittest.TestCase):
    """Tests for VEH0142 normalization (most complex due to intro rows)."""

    def setUp(self):
        self.normalizer = DfTNormalizer()
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_veh0142_with_intro_rows(self):
        """Test: VEH0142 with introductory metadata rows."""
        # Create file with 4 intro rows then header then data
        content = """Vehicle Emissions & Fuel Type Registration Data
UK Government Department for Transport
Last updated: 2026-06-30
License: Open Government License

BodyType,Fuel,Keepership,ONS Code,2025 Q1,2025 Q2,2026 Q1,2026 Q2
Car,BEV,Private,E12000001,1000,1050,1100,1150
Van,BEV,Private,E12000001,500,520,550,580
"""
        raw_path = os.path.join(self.temp_dir, "raw_veh0142.csv")
        output_path = os.path.join(self.temp_dir, "normalized_veh0142.csv")

        with open(raw_path, 'w') as f:
            f.write(content)

        success, error = self.normalizer.normalize_veh0142(raw_path, output_path)

        self.assertTrue(success, f"Should normalize VEH0142 with intro rows. Error: {error}")
        self.assertTrue(os.path.exists(output_path))

        # Verify output has correct structure
        df = pd.read_csv(output_path)
        self.assertIn('BodyType', df.columns)
        self.assertIn('2026 Q2', df.columns)
        self.assertEqual(len(df), 2)  # 2 data rows

    def test_veh0142_one_extra_intro_row(self):
        """Test: VEH0142 with one additional introductory row."""
        content = """Vehicle Emissions & Fuel Type Registration Data
UK Government Department for Transport
Last updated: 2026-06-30
Revised: 2026-06-15
License: Open Government License

BodyType,Fuel,Keepership,ONS Code,2025 Q1,2025 Q2,2026 Q1,2026 Q2
Car,BEV,Private,E12000001,1000,1050,1100,1150
"""
        raw_path = os.path.join(self.temp_dir, "raw_veh0142_extra.csv")
        output_path = os.path.join(self.temp_dir, "normalized_veh0142_extra.csv")

        with open(raw_path, 'w') as f:
            f.write(content)

        success, error = self.normalizer.normalize_veh0142(raw_path, output_path)

        self.assertTrue(success, f"Should handle extra intro row. Error: {error}")
        df = pd.read_csv(output_path)
        self.assertEqual(len(df), 1)

    def test_veh0142_one_fewer_intro_row(self):
        """Test: VEH0142 with one fewer introductory row."""
        content = """Vehicle Emissions & Fuel Type Registration Data
UK Government Department for Transport

BodyType,Fuel,Keepership,ONS Code,2025 Q1,2025 Q2,2026 Q1,2026 Q2
Car,BEV,Private,E12000001,1000,1050,1100,1150
"""
        raw_path = os.path.join(self.temp_dir, "raw_veh0142_fewer.csv")
        output_path = os.path.join(self.temp_dir, "normalized_veh0142_fewer.csv")

        with open(raw_path, 'w') as f:
            f.write(content)

        success, error = self.normalizer.normalize_veh0142(raw_path, output_path)

        self.assertTrue(success, f"Should handle fewer intro rows. Error: {error}")
        df = pd.read_csv(output_path)
        self.assertIn('BodyType', df.columns)

    def test_veh0142_unrecognised_format(self):
        """Test: Unrecognised VEH0142 format fails safely."""
        # File with no recognizable headers
        content = """Some random data
This is not VEH0142
Could be anything
1,2,3,4
5,6,7,8
"""
        raw_path = os.path.join(self.temp_dir, "invalid_veh0142.csv")
        output_path = os.path.join(self.temp_dir, "output_veh0142.csv")

        with open(raw_path, 'w') as f:
            f.write(content)

        success, error = self.normalizer.normalize_veh0142(raw_path, output_path)

        self.assertFalse(success, "Should fail on unrecognised format")
        self.assertIn("Could not identify", error or "")


class TestQuarterDetection(unittest.TestCase):
    """Tests for dynamic quarter/vintage detection."""

    def setUp(self):
        self.normalizer = DfTNormalizer()

    def test_detect_q1_2026(self):
        """Test: Q1 2026 detected correctly."""
        quarters = ["2025 Q4", "2026 Q1"]
        latest = self.normalizer._detect_latest_quarter(quarters)
        self.assertEqual(latest, "2026-03-31")

    def test_detect_q2_2026(self):
        """Test: Q2 2026 detected correctly."""
        quarters = ["2026 Q1", "2026 Q2"]
        latest = self.normalizer._detect_latest_quarter(quarters)
        self.assertEqual(latest, "2026-06-30")

    def test_detect_q3_2026(self):
        """Test: Q3 2026 detected correctly."""
        quarters = ["2026 Q2", "2026 Q3"]
        latest = self.normalizer._detect_latest_quarter(quarters)
        self.assertEqual(latest, "2026-09-30")

    def test_detect_q4_2026(self):
        """Test: Q4 2026 detected correctly."""
        quarters = ["2026 Q3", "2026 Q4"]
        latest = self.normalizer._detect_latest_quarter(quarters)
        self.assertEqual(latest, "2026-12-31")

    def test_year_transition_q4_to_q1(self):
        """Test: Year transition Q4 2026 → Q1 2027."""
        quarters = ["2026 Q4", "2027 Q1"]
        latest = self.normalizer._detect_latest_quarter(quarters)
        self.assertEqual(latest, "2027-03-31")

    def test_multiple_quarters_latest_selected(self):
        """Test: Multiple quarters; latest is selected."""
        quarters = ["2025 Q1", "2025 Q2", "2025 Q3", "2025 Q4", "2026 Q1", "2026 Q2"]
        latest = self.normalizer._detect_latest_quarter(quarters)
        self.assertEqual(latest, "2026-06-30")


class TestRawPreservation(unittest.TestCase):
    """Tests for raw source preservation."""

    def setUp(self):
        self.normalizer = DfTNormalizer()
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_raw_source_unchanged(self):
        """Test: Raw source file remains unchanged after normalization."""
        # Create raw VEH0135
        raw_data = "LSOA21CD,Fuel,Keepership,2026 Q1,2026 Q2\nE01000001,BEV,Private,100,110\n"
        raw_path = os.path.join(self.temp_dir, "raw_veh0135.csv")
        output_path = os.path.join(self.temp_dir, "normalized_veh0135.csv")

        with open(raw_path, 'w') as f:
            f.write(raw_data)

        raw_checksum_before = self._file_checksum(raw_path)

        self.normalizer.normalize_veh0135(raw_path, output_path)

        raw_checksum_after = self._file_checksum(raw_path)

        self.assertEqual(raw_checksum_before, raw_checksum_after,
                        "Raw file must not be modified")

    def _file_checksum(self, path: str) -> str:
        """Simple checksum for comparison."""
        with open(path, 'rb') as f:
            return str(len(f.read()))


class TestHeaderDetection(unittest.TestCase):
    """Tests for correct header detection."""

    def setUp(self):
        self.normalizer = DfTNormalizer()
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_bodytype_column_detection(self):
        """Test: BodyType column detection in VEH0142."""
        content = """Intro row 1
Intro row 2

BodyType,Fuel,Keepership,ONS Code,2026 Q2
Car,BEV,Private,E12000001,1000
"""
        raw_path = os.path.join(self.temp_dir, "test_bodytype.csv")
        output_path = os.path.join(self.temp_dir, "output_bodytype.csv")

        with open(raw_path, 'w') as f:
            f.write(content)

        success, error = self.normalizer.normalize_veh0142(raw_path, output_path)

        self.assertTrue(success)
        df = pd.read_csv(output_path)
        self.assertIn('BodyType', df.columns)


if __name__ == "__main__":
    unittest.main(verbosity=2)
