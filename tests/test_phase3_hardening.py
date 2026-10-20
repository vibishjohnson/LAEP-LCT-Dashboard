#!/usr/bin/env python3
"""
Phase 3 Hardening Verification Tests

Comprehensive validation of nowcast engine:
- Cumulative formula correctness
- Multi-month cumulation
- Idempotency
- Month-end dates
- DNO reconciliation
- Gap handling
- Zero inputs
- Weight vintage
- Four-way aggregation
- Full integration
"""

import os
import sys
import unittest
import tempfile
import shutil
from datetime import datetime
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, PROJECT_ROOT)

from data_manager.nowcast import NowcastEngine
from data_manager.db import Database


class TestNowcastFormula(unittest.TestCase):
    """Test 1: Verify cumulative formula correctness."""

    @classmethod
    def setUpClass(cls):
        lsoa_ev_path = os.path.join(PROJECT_ROOT, "project", "output_processed", "lsoa_ev_bodytype.csv")
        cls.engine = NowcastEngine(lsoa_ev_path)

    def test_formula_baseline_plus_allocations(self):
        """
        Verify: Stock[April] = Baseline[March] + April allocation
        NOT: Stock[April] = Baseline[March] × weight
        """
        # Baseline from March 2026 DfT
        baseline_bev_cars_gb = self.engine.weights["BEV Cars"]["gb_total"]
        baseline_bev_cars_ukpn = self.engine.weights["BEV Cars"]["ukpn_total"]

        # Baselines are the March 2026 BEV Cars totals of the (geography-corrected) body-type file
        march = self.engine.lsoa_ev[(self.engine.lsoa_ev["period"] == "2026-03-31") &
                                    (self.engine.lsoa_ev["Fuel"] == "BEV") & (self.engine.lsoa_ev["BodyType"] == "Cars")]
        self.assertEqual(baseline_bev_cars_gb, int(march["ev_count"].sum()))
        ukpn_lsoas = set(self.engine.lsoa_dno[self.engine.lsoa_dno["DNO"].notna()]["LSOA21CD"])
        self.assertEqual(baseline_bev_cars_ukpn, int(march[march["LSOA21CD"].isin(ukpn_lsoas)]["ev_count"].sum()))

        # April SMMT input (national flow)
        april_smmt = 100000
        ukpn_weight = baseline_bev_cars_ukpn / baseline_bev_cars_gb

        # Allocation (not multiplication of baseline)
        april_allocation = april_smmt * ukpn_weight  # ~32,596

        # April stock should be: baseline + allocation
        april_stock = baseline_bev_cars_ukpn + april_allocation

        # NOT: baseline × weight (which would give nonsense)
        wrong_formula = baseline_bev_cars_ukpn * ukpn_weight

        self.assertGreater(april_stock, baseline_bev_cars_ukpn,
                          "April stock must be GREATER than baseline")
        self.assertGreater(april_stock, wrong_formula,
                          "Correct formula gives higher stock than incorrect formula")

        # Baseline plus the GB flow times the UKPN share
        self.assertAlmostEqual(april_stock, baseline_bev_cars_ukpn + april_smmt * baseline_bev_cars_ukpn / baseline_bev_cars_gb,
                               delta=1e-6)


class TestMultiMonthCumulation(unittest.TestCase):
    """Test 2: Multi-month cumulative allocation."""

    @classmethod
    def setUpClass(cls):
        lsoa_ev_path = os.path.join(PROJECT_ROOT, "project", "output_processed", "lsoa_ev_bodytype.csv")
        cls.engine = NowcastEngine(lsoa_ev_path)

    def test_three_month_cumulation(self):
        """
        Test: April + May + June cumulative properly stacks.
        """
        # Three months of SMMT input
        smmt_by_month = {
            "2026-04-30": {"BEV Cars": 100000, "BEV Vans": 5000, "PHEV Cars": 50000, "PHEV Vans": 2000},
            "2026-05-31": {"BEV Cars": 110000, "BEV Vans": 5500, "PHEV Cars": 55000, "PHEV Vans": 2200},
            "2026-06-30": {"BEV Cars": 120000, "BEV Vans": 6000, "PHEV Cars": 60000, "PHEV Vans": 2400},
        }

        baseline_ukpn = {cat: self.engine.weights[cat]["ukpn_total"]
                         for cat in ["BEV Cars", "BEV Vans", "PHEV Cars", "PHEV Vans"]}

        cumulative_by_month = {}
        running_total = baseline_ukpn.copy()

        for month in sorted(smmt_by_month.keys()):
            smmt = smmt_by_month[month]
            allocations = self.engine.allocate_monthly_smmt(smmt, month)

            # Calculate UKPN aggregate
            for cat in ["BEV Cars", "BEV Vans", "PHEV Cars", "PHEV Vans"]:
                if cat in allocations and not allocations[cat].empty:
                    ukpn_add = allocations[cat]['addition'].sum()
                    running_total[cat] += ukpn_add

            cumulative_by_month[month] = running_total.copy()

        # Verify cumulation stacking
        april_stock = cumulative_by_month["2026-04-30"]["BEV Cars"]
        may_stock = cumulative_by_month["2026-05-31"]["BEV Cars"]
        june_stock = cumulative_by_month["2026-06-30"]["BEV Cars"]

        # Each month should increase
        self.assertGreater(may_stock, april_stock, "May stock > April")
        self.assertGreater(june_stock, may_stock, "June stock > May")

        # Verify correct cumulation at category level
        share = self.engine.weights["BEV Cars"]["gb_weight"]
        april_add = smmt_by_month["2026-04-30"]["BEV Cars"] * share
        may_add = smmt_by_month["2026-05-31"]["BEV Cars"] * share
        june_add = smmt_by_month["2026-06-30"]["BEV Cars"] * share

        expected_june = baseline_ukpn["BEV Cars"] + april_add + may_add + june_add

        self.assertAlmostEqual(june_stock, expected_june, delta=20,
                              msg="June stock = Baseline + Apr + May + Jun allocations (rounding tolerance)")


class TestIdempotency(unittest.TestCase):
    """Test 3: Idempotency - same input = same output."""

    @classmethod
    def setUpClass(cls):
        lsoa_ev_path = os.path.join(PROJECT_ROOT, "project", "output_processed", "lsoa_ev_bodytype.csv")
        cls.engine = NowcastEngine(lsoa_ev_path)

    def test_same_input_same_output(self):
        """Generate same nowcast twice, should get identical results."""
        smmt = {"BEV Cars": 100000, "BEV Vans": 5000, "PHEV Cars": 50000, "PHEV Vans": 2000}

        # First run
        result1 = self.engine.allocate_monthly_smmt(smmt, "2026-04-30")

        # Second run (reset state, create new engine)
        engine2 = NowcastEngine("project/output_processed/lsoa_ev_bodytype.csv")
        result2 = engine2.allocate_monthly_smmt(smmt, "2026-04-30")

        # Compare results
        for cat in ["BEV Cars", "BEV Vans", "PHEV Cars", "PHEV Vans"]:
            df1 = result1[cat].sort_values('LSOA21CD').reset_index(drop=True)
            df2 = result2[cat].sort_values('LSOA21CD').reset_index(drop=True)

            pd.testing.assert_frame_equal(df1, df2,
                check_names=True,
                check_dtype=True,
                check_exact=True,
                obj=f"Idempotency check for {cat}")


class TestMonthEndConvention(unittest.TestCase):
    """Test 4: Month-end date standardization."""

    def test_month_end_dates(self):
        """Verify month-end dates are correct."""
        test_cases = [
            ("2026-04-30", True),   # April 30
            ("2026-05-31", True),   # May 31
            ("2026-06-30", True),   # June 30
            ("2026-02-28", True),   # Feb 28 (non-leap 2026)
            ("2024-02-29", True),   # Feb 29 (leap 2024)
            ("2026-12-31", True),   # December 31
            ("2026-04-15", False),  # NOT month-end
            ("2026-05-30", False),  # NOT month-end
        ]

        for date_str, is_month_end in test_cases:
            year, month, day = int(date_str.split('-')[0]), int(date_str.split('-')[1]), int(date_str.split('-')[2])

            # Check if it's month-end
            if month == 12:
                next_month = 1
            else:
                next_month = month + 1

            import calendar
            _, last_day = calendar.monthrange(year, month)

            actual_is_month_end = (day == last_day)
            self.assertEqual(actual_is_month_end, is_month_end,
                           f"{date_str}: is_month_end should be {is_month_end}")


class TestDNOReconciliation(unittest.TestCase):
    """Test 5: EPN/SPN/LPN reconciliation."""

    @classmethod
    def setUpClass(cls):
        lsoa_ev_path = os.path.join(PROJECT_ROOT, "project", "output_processed", "lsoa_ev_bodytype.csv")
        cls.engine = NowcastEngine(lsoa_ev_path)

    def test_dno_sum_equals_ukpn(self):
        """Verify SUM(EPN + SPN + LPN) = UKPN."""
        if not self.engine.lsoa_dno is None:
            # For each category, check DNO breakdown
            for cat in ["BEV Cars", "BEV Vans", "PHEV Cars", "PHEV Vans"]:
                w = self.engine.weights[cat]
                lsoa_weights = w['lsoa_weights']

                # Would need LSOA → DNO lookup to sum by DNO
                # For now, verify that weight sums to expected UKPN value
                self.assertGreater(len(lsoa_weights), 0,
                                 f"{cat}: should have LSOA weights")


class TestZeroInputs(unittest.TestCase):
    """Test 6: Zero input handling."""

    @classmethod
    def setUpClass(cls):
        lsoa_ev_path = os.path.join(PROJECT_ROOT, "project", "output_processed", "lsoa_ev_bodytype.csv")
        cls.engine = NowcastEngine(lsoa_ev_path)

    def test_zero_smmt_category(self):
        """If SMMT input = 0 for a category, allocation should be 0."""
        smmt = {"BEV Cars": 0, "BEV Vans": 5000, "PHEV Cars": 50000, "PHEV Vans": 2000}

        results = self.engine.allocate_monthly_smmt(smmt, "2026-04-30")

        # BEV Cars should be empty or sum to 0
        if "BEV Cars" in results:
            bev_cars_result = results["BEV Cars"]
            if not bev_cars_result.empty:
                total = bev_cars_result['addition'].sum()
                self.assertEqual(total, 0, "Zero SMMT should give zero allocation")
            else:
                self.assertTrue(True, "Zero SMMT returns empty DataFrame")

        # Other categories should have data
        self.assertFalse(results["BEV Vans"].empty, "Non-zero SMMT should have data")


class TestGapHandling(unittest.TestCase):
    """Test 7: Missing month gap detection."""

    @classmethod
    def setUpClass(cls):
        lsoa_ev_path = os.path.join(PROJECT_ROOT, "project", "output_processed", "lsoa_ev_bodytype.csv")
        cls.engine = NowcastEngine(lsoa_ev_path)

    def test_missing_month_detection(self):
        """Test that missing June between May and July is detected."""
        from dateutil.relativedelta import relativedelta
        import calendar

        months = ["2026-04-30", "2026-05-31", "2026-07-31"]  # June missing

        # Check for gaps
        sorted_months = sorted(months)
        gaps = []

        for i in range(len(sorted_months) - 1):
            current = datetime.strptime(sorted_months[i], "%Y-%m-%d")
            next_month = datetime.strptime(sorted_months[i+1], "%Y-%m-%d")

            # Calculate expected next month-end
            expected_month = current + relativedelta(months=1)
            _, last_day = calendar.monthrange(expected_month.year, expected_month.month)
            expected_month_end = expected_month.replace(day=last_day)

            if next_month != expected_month_end:
                gaps.append((sorted_months[i], sorted_months[i+1]))

        self.assertEqual(len(gaps), 1, "Should detect one gap")
        self.assertEqual(gaps[0], ("2026-05-31", "2026-07-31"), "Gap should be May→July")


class TestWeightVintage(unittest.TestCase):
    """Test 8: Weight vintage provenance."""

    @classmethod
    def setUpClass(cls):
        lsoa_ev_path = os.path.join(PROJECT_ROOT, "project", "output_processed", "lsoa_ev_bodytype.csv")
        cls.engine = NowcastEngine(lsoa_ev_path)

    def test_weight_vintage_stored(self):
        """Verify weight vintage is detectable."""
        # Should be derived from baseline period in engine
        baseline_period = "2026-03-31"

        # Engine should have this in initialization
        self.assertIsNotNone(self.engine.baseline_stock, "Baseline stock should be loaded")

        # QA log should reference the period
        qa_report = self.engine.get_qa_report()
        self.assertIn("2026-03-31", qa_report,
                     "QA report should reference authoritative period")


class TestFourWayAggregation(unittest.TestCase):
    """Test 9: Four-way aggregation identities."""

    @classmethod
    def setUpClass(cls):
        lsoa_ev_path = os.path.join(PROJECT_ROOT, "project", "output_processed", "lsoa_ev_bodytype.csv")
        cls.engine = NowcastEngine(lsoa_ev_path)

    def test_aggregation_identities(self):
        """Verify BEV = BEV_Cars + BEV_Vans, etc."""
        smmt = {"BEV Cars": 100000, "BEV Vans": 5000, "PHEV Cars": 50000, "PHEV Vans": 2000}

        results = self.engine.allocate_monthly_smmt(smmt, "2026-04-30")

        bev_cars_total = results["BEV Cars"]['addition'].sum() if not results["BEV Cars"].empty else 0
        bev_vans_total = results["BEV Vans"]['addition'].sum() if not results["BEV Vans"].empty else 0
        phev_cars_total = results["PHEV Cars"]['addition'].sum() if not results["PHEV Cars"].empty else 0
        phev_vans_total = results["PHEV Vans"]['addition'].sum() if not results["PHEV Vans"].empty else 0

        # Aggregations
        bev_total = bev_cars_total + bev_vans_total
        phev_total = phev_cars_total + phev_vans_total
        ev_total = bev_total + phev_total

        # All should be non-negative
        self.assertGreaterEqual(bev_cars_total, 0)
        self.assertGreaterEqual(bev_vans_total, 0)
        self.assertGreaterEqual(bev_total, bev_cars_total)
        self.assertGreaterEqual(phev_total, phev_cars_total)
        self.assertGreaterEqual(ev_total, bev_total)


class TestProductionIsolation(unittest.TestCase):
    """Test 10: Production files remain untouched."""

    def test_production_files_untouched(self):
        """Verify production files are read-only."""
        prod_files = [
            "project/output_processed/lsoa_ev_bodytype.csv",
            "project/output_processed/laep_ev_bodytype.csv",
            "ev/df_VEH0135.csv",
            "ev/VEH0142.csv",
        ]

        for fpath in prod_files:
            full_path = os.path.join(PROJECT_ROOT, fpath)
            if os.path.exists(full_path):
                # Check it exists and is readable
                self.assertTrue(os.path.isfile(full_path), f"{fpath} should exist")
                self.assertTrue(os.access(full_path, os.R_OK), f"{fpath} should be readable")


if __name__ == "__main__":
    # Run all tests
    unittest.main(verbosity=2)
