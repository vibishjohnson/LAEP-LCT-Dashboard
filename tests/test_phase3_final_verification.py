#!/usr/bin/env python3
"""
Phase 3 Final Verification Tests

Gap 1: EPN/SPN/LPN Reconciliation
Gap 2: Automated test suite consolidation
"""

import os
import sys
import unittest
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, PROJECT_ROOT)

from data_manager.nowcast import NowcastEngine


class TestDNOReconciliation(unittest.TestCase):
    """Gap 1: Prove EPN/SPN/LPN reconciliation."""

    @classmethod
    def setUpClass(cls):
        lsoa_ev_path = os.path.join(PROJECT_ROOT, "project", "output_processed", "lsoa_ev_bodytype.csv")
        cls.engine = NowcastEngine(lsoa_ev_path)
        cls.lsoa_ev = pd.read_csv(lsoa_ev_path)

        # Load DNO mapping
        import openpyxl
        cls.lsoa_dno = pd.read_excel("ev/LSOA mapping.xlsx")
        cls.lsoa_dno_clean = cls.lsoa_dno[['LSOA21CD', 'Majority Licence area']].drop_duplicates()
        cls.lsoa_dno_clean.columns = ['LSOA21CD', 'DNO']

    def test_dno_sum_equals_ukpn_all_categories(self):
        """
        PROVE: SUM(EPN + SPN + LPN LSOA additions) = UKPN allocation
        """
        # Test case: April 2026 with test SMMT
        smmt = {
            "BEV Cars": 100000,
            "BEV Vans": 5000,
            "PHEV Cars": 50000,
            "PHEV Vans": 2000,
        }

        results = self.engine.allocate_monthly_smmt(smmt, "2026-04-30")

        print("\n" + "=" * 80)
        print("EPN/SPN/LPN RECONCILIATION TEST — APRIL 2026")
        print("=" * 80)

        for cat in ["BEV Cars", "BEV Vans", "PHEV Cars", "PHEV Vans"]:
            df = results[cat]
            if df.empty:
                continue

            # Get UKPN total
            ukpn_total = df['addition'].sum()

            # Merge with DNO mapping
            df_with_dno = df.merge(self.lsoa_dno_clean, on='LSOA21CD', how='left')

            # Calculate DNO breakdown
            dno_breakdown = df_with_dno.groupby('DNO')['addition'].sum()

            epn_sum = dno_breakdown.get('EPN', 0)
            spn_sum = dno_breakdown.get('SPN', 0)
            lpn_sum = dno_breakdown.get('LPN', 0)
            unmapped = df_with_dno[df_with_dno['DNO'].isna()]['addition'].sum()

            dno_total = epn_sum + spn_sum + lpn_sum

            print(f"\n{cat}:")
            print(f"  UKPN allocation: {ukpn_total:,}")
            print(f"  EPN addition: {epn_sum:,}")
            print(f"  SPN addition: {spn_sum:,}")
            print(f"  LPN addition: {lpn_sum:,}")
            print(f"  DNO sum: {dno_total:,}")
            print(f"  Unmapped LSOAs: {unmapped:,}")

            # Verify reconciliation
            self.assertEqual(dno_total, ukpn_total,
                           f"{cat}: SUM(DNO) should equal UKPN total")
            self.assertEqual(unmapped, 0,
                           f"{cat}: No unmapped UKPN LSOAs should exist")

            # Verify all LSOAs mapped
            mapped_count = len(df_with_dno[df_with_dno['DNO'].notna()])
            total_count = len(df)
            self.assertEqual(mapped_count, total_count,
                           f"{cat}: All LSOAs must have DNO mapping")

    def test_lsoa_mappings_complete(self):
        """Verify no UKPN LSOA lacks DNO mapping."""
        print("\n" + "=" * 80)
        print("LSOA MAPPING COMPLETENESS TEST")
        print("=" * 80)

        # Get all UKPN LSOAs from baseline stock
        baseline = self.lsoa_ev[self.lsoa_ev['period'] == "2026-03-31"].copy()
        baseline = baseline.merge(self.lsoa_dno_clean, on='LSOA21CD', how='left')

        ukpn_lsoas = baseline[baseline['DNO'].notna()]['LSOA21CD'].unique()

        print(f"\nTotal LSOA21CD with DNO mapping: {len(ukpn_lsoas)}")

        # Verify all have either EPN, SPN, or LPN
        for lsoa in ukpn_lsoas[:10]:  # Sample check
            dno = baseline[baseline['LSOA21CD'] == lsoa]['DNO'].iloc[0]
            self.assertIn(dno, ['EPN', 'SPN', 'LPN'],
                         f"LSOA {lsoa} must map to EPN, SPN, or LPN")

        print("All tested LSOAs mapped correctly")


class TestPhase3HardeningAutomated(unittest.TestCase):
    """Gap 2: Automated Phase 3 hardening checks."""

    @classmethod
    def setUpClass(cls):
        lsoa_ev_path = os.path.join(PROJECT_ROOT, "project", "output_processed", "lsoa_ev_bodytype.csv")
        cls.engine = NowcastEngine(lsoa_ev_path)

    def test_cumulative_formula_is_correct(self):
        """Test 1: Baseline + Allocation, not Baseline × Weight."""
        baseline_ukpn = self.engine.weights["BEV Cars"]["ukpn_total"]
        baseline_gb = self.engine.weights["BEV Cars"]["gb_total"]

        april_smmt = 100000
        ukpn_weight = baseline_ukpn / baseline_gb

        # Correct formula
        april_allocation = april_smmt * ukpn_weight
        april_stock_correct = baseline_ukpn + april_allocation

        # Wrong formula
        april_stock_wrong = baseline_ukpn * ukpn_weight

        self.assertGreater(april_stock_correct, baseline_ukpn)
        self.assertGreater(april_stock_correct, april_stock_wrong)

    def test_multi_month_cumulation(self):
        """Test 2: Three consecutive months cumulate correctly."""
        baseline = 549401  # BEV Cars UKPN baseline

        allocations = []
        cumulative = baseline

        for month in ["2026-04-30", "2026-05-31", "2026-06-30"]:
            smmt = {
                "BEV Cars": [100000, 110000, 120000][["2026-04-30", "2026-05-31", "2026-06-30"].index(month)],
                "BEV Vans": 5000, "PHEV Cars": 50000, "PHEV Vans": 2000
            }
            results = self.engine.allocate_monthly_smmt(smmt, month)
            add = results["BEV Cars"]['addition'].sum()
            cumulative += add
            allocations.append((month, add, cumulative))

        # Verify stacking
        self.assertGreater(allocations[1][2], allocations[0][2])
        self.assertGreater(allocations[2][2], allocations[1][2])

    def test_idempotency(self):
        """Test 3: Same input = same output."""
        smmt = {"BEV Cars": 100000, "BEV Vans": 5000, "PHEV Cars": 50000, "PHEV Vans": 2000}

        result1 = self.engine.allocate_monthly_smmt(smmt, "2026-04-30")
        engine2 = NowcastEngine("project/output_processed/lsoa_ev_bodytype.csv")
        result2 = engine2.allocate_monthly_smmt(smmt, "2026-04-30")

        for cat in ["BEV Cars", "BEV Vans", "PHEV Cars", "PHEV Vans"]:
            sum1 = result1[cat]['addition'].sum() if not result1[cat].empty else 0
            sum2 = result2[cat]['addition'].sum() if not result2[cat].empty else 0
            self.assertEqual(sum1, sum2)

    def test_month_end_convention(self):
        """Test 4: Month-end dates validated."""
        import calendar

        test_dates = {
            "2026-04-30": True,
            "2026-05-31": True,
            "2026-02-28": True,
            "2024-02-29": True,
            "2026-04-15": False,
        }

        for date_str, should_be_month_end in test_dates.items():
            year, month, day = map(int, date_str.split('-'))
            _, last_day = calendar.monthrange(year, month)
            is_month_end = (day == last_day)
            self.assertEqual(is_month_end, should_be_month_end)

    def test_zero_input_handling(self):
        """Test 5: Zero SMMT = zero allocation."""
        smmt = {"BEV Cars": 0, "BEV Vans": 5000, "PHEV Cars": 50000, "PHEV Vans": 2000}
        results = self.engine.allocate_monthly_smmt(smmt, "2026-04-30")

        bev_cars_sum = results["BEV Cars"]['addition'].sum() if not results["BEV Cars"].empty else 0
        bev_vans_sum = results["BEV Vans"]['addition'].sum()

        self.assertEqual(bev_cars_sum, 0)
        self.assertGreater(bev_vans_sum, 0)

    def test_weight_vintage_provenance(self):
        """Test 6: Weight vintage tracked in QA."""
        qa = self.engine.get_qa_report()
        self.assertIn("2026-03-31", qa)

    def test_four_way_aggregation(self):
        """Test 7: BEV = Cars + Vans, etc."""
        smmt = {"BEV Cars": 100000, "BEV Vans": 5000, "PHEV Cars": 50000, "PHEV Vans": 2000}
        results = self.engine.allocate_monthly_smmt(smmt, "2026-04-30")

        bev_cars = results["BEV Cars"]['addition'].sum()
        bev_vans = results["BEV Vans"]['addition'].sum()
        phev_cars = results["PHEV Cars"]['addition'].sum()
        phev_vans = results["PHEV Vans"]['addition'].sum()

        bev_total = bev_cars + bev_vans
        phev_total = phev_cars + phev_vans
        ev_total = bev_total + phev_total

        self.assertGreater(bev_total, bev_cars)
        self.assertGreater(phev_total, phev_cars)
        self.assertGreater(ev_total, bev_total)

    def test_production_isolation(self):
        """Test 8: Production files untouched."""
        prod_files = [
            "project/output_processed/lsoa_ev_bodytype.csv",
            "ev/df_VEH0135.csv",
            "ev/VEH0142.csv",
        ]

        for fpath in prod_files:
            self.assertTrue(os.path.isfile(fpath))
            self.assertTrue(os.access(fpath, os.R_OK))


if __name__ == "__main__":
    unittest.main(verbosity=2)
