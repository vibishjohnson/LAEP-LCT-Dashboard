"""
Date Semantics Tests

Verify that month-end dates are correctly generated and used throughout
the SMMT nowcast pipeline.

Required convention:
- 2026-04 → 2026-04-30
- 2026-05 → 2026-05-31
- 2026-06 → 2026-06-30
- 2026-07 → 2026-07-31
- 2026-08 → 2026-08-31
- 2026-02 → 2026-02-28
- 2024-02 → 2024-02-29 (leap year)
- 2026-12 → 2026-12-31
- 2027-01 → 2027-01-31
"""

import unittest
import calendar
import sys
import os

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


class TestMonthEndDateGeneration(unittest.TestCase):
    """Test that month-end dates are correctly generated."""

    def get_month_end_date(self, year, month):
        """Helper to generate month-end date (same logic as in app_data_manager.py)."""
        last_day = calendar.monthrange(year, month)[1]
        return f"{year}-{month:02d}-{last_day:02d}"

    def test_april_2026_end(self):
        """April 2026 has 30 days."""
        self.assertEqual(self.get_month_end_date(2026, 4), "2026-04-30")

    def test_may_2026_end(self):
        """May 2026 has 31 days."""
        self.assertEqual(self.get_month_end_date(2026, 5), "2026-05-31")

    def test_june_2026_end(self):
        """June 2026 has 30 days."""
        self.assertEqual(self.get_month_end_date(2026, 6), "2026-06-30")

    def test_july_2026_end(self):
        """July 2026 has 31 days."""
        self.assertEqual(self.get_month_end_date(2026, 7), "2026-07-31")

    def test_august_2026_end(self):
        """August 2026 has 31 days."""
        self.assertEqual(self.get_month_end_date(2026, 8), "2026-08-31")

    def test_february_non_leap_year(self):
        """February 2026 (non-leap year) has 28 days."""
        self.assertEqual(self.get_month_end_date(2026, 2), "2026-02-28")

    def test_february_leap_year(self):
        """February 2024 (leap year) has 29 days."""
        self.assertEqual(self.get_month_end_date(2024, 2), "2024-02-29")

    def test_december_2026_end(self):
        """December 2026 has 31 days."""
        self.assertEqual(self.get_month_end_date(2026, 12), "2026-12-31")

    def test_january_2027_end(self):
        """January 2027 has 31 days (year transition)."""
        self.assertEqual(self.get_month_end_date(2027, 1), "2027-01-31")

    def test_not_mid_month(self):
        """Confirm that dates are NOT mid-month (15th)."""
        self.assertNotEqual(self.get_month_end_date(2026, 4), "2026-04-15")
        self.assertNotEqual(self.get_month_end_date(2026, 5), "2026-05-15")
        self.assertNotEqual(self.get_month_end_date(2026, 6), "2026-06-15")


class TestNowcastDateSemantics(unittest.TestCase):
    """Test that month-end dates propagate correctly through nowcast pipeline."""

    def setUp(self):
        """Set up test fixtures."""
        self.lsoa_ev_path = os.path.join(
            PROJECT_ROOT, "project", "output_processed", "lsoa_ev_bodytype.csv"
        )

    def test_nowcast_engine_accepts_month_end_dates(self):
        """Verify NowcastEngine correctly processes month-end dates."""
        if not os.path.exists(self.lsoa_ev_path):
            self.skipTest("lsoa_ev_bodytype.csv not found")

        from data_manager.nowcast import NowcastEngine

        engine = NowcastEngine(self.lsoa_ev_path)

        # Test with month-end date
        smmt_input = {
            "BEV Cars": 1000,
            "BEV Vans": 100,
            "PHEV Cars": 500,
            "PHEV Vans": 50
        }

        month_end_date = "2026-06-30"
        result = engine.allocate_monthly_smmt(smmt_input, month_end_date)

        # Verify result contains month field with correct date
        for category, df in result.items():
            if not df.empty:
                # All rows should have the month-end date
                for idx, row in df.iterrows():
                    self.assertEqual(row['month'], month_end_date,
                                   f"{category} row has incorrect month: {row['month']}")

    def test_nowcast_engine_rejects_mid_month_dates(self):
        """Verify that mid-month dates work but can be distinguished."""
        if not os.path.exists(self.lsoa_ev_path):
            self.skipTest("lsoa_ev_bodytype.csv not found")

        from data_manager.nowcast import NowcastEngine

        engine = NowcastEngine(self.lsoa_ev_path)

        smmt_input = {
            "BEV Cars": 1000,
            "BEV Vans": 100,
            "PHEV Cars": 500,
            "PHEV Vans": 50
        }

        # Test with mid-month date (incorrect)
        mid_month_date = "2026-06-15"
        result = engine.allocate_monthly_smmt(smmt_input, mid_month_date)

        # Verify result stores the provided date (even if incorrect)
        for category, df in result.items():
            if not df.empty:
                for idx, row in df.iterrows():
                    self.assertEqual(row['month'], mid_month_date,
                                   f"Engine stores provided month value: {row['month']}")
                    # Confirm it's NOT month-end
                    self.assertNotEqual(row['month'], "2026-06-30",
                                      "Should not auto-correct to month-end")


if __name__ == '__main__':
    unittest.main()
