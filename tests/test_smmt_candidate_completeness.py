"""
SMMT Candidate Completeness Tests

A freshly generated SMMT nowcast candidate (temporary folder) must be complete and drop-in compatible with the
dashboard's monthly forecast files, without containing or changing any actual:
1. Every expected file exists and matches its sealed hash
2. Forecast-file schema identical to the live monthly_forecast_* files
3. Every DNO / LA / LAEP x fuel x nowcast month present exactly once
4. Only SMMT_NOWCAST rows, monthly, month-end dates, non-negative stock
5. Preview files = live forecast rows (unchanged) + the candidate rows
6. Manifest records provenance for every input
"""

import json
import os
import sys
import tempfile
import unittest

import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from data_manager.smmt_candidate_builder import (DATA_FILES, FORECAST_SOURCE, INTEGRATION_REL,  # noqa: E402
                                                 PREVIEW_FILES, SMmtCandidateBuilder)

INTEG = os.path.join(PROJECT_ROOT, *INTEGRATION_REL.split("/"))
GEO_COL = {"dno": "DNO", "la": "LAD22CD", "laep": "LAEP"}


class TestSMMTCandidateCompleteness(unittest.TestCase):
    """Test SMMT candidate completeness."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.builder = SMmtCandidateBuilder(PROJECT_ROOT)
        cls.builder.candidates_dir = cls.tmp.name
        cls.m = cls.builder.generate()
        cls.folder = cls.builder.folder(cls.m["candidate_id"])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def read(self, rel, **kw):
        return pd.read_csv(os.path.join(self.folder, *rel.split("/")), dtype={"LAD22CD": str}, **kw)

    def test_1_candidate_files_exist_and_sealed(self):
        self.assertEqual(self.m["status"], "VALIDATED", self.m.get("errors"))
        self.assertEqual(sorted(self.m["files_sha256"]), sorted(DATA_FILES + PREVIEW_FILES))
        ok, detail = self.builder.verify(self.m["candidate_id"])
        self.assertTrue(ok, detail)

    def test_2_schema_matches_live_forecast_files(self):
        for g in GEO_COL:
            live = pd.read_csv(os.path.join(INTEG, f"monthly_forecast_{g}.csv"), nrows=0)
            self.assertEqual(list(self.read(f"smmt_nowcast_{g}.csv").columns), list(live.columns))

    def test_3_every_geography_fuel_month_present_once(self):
        months = self.m["nowcast_months"]
        for g, col in GEO_COL.items():
            df = self.read(f"smmt_nowcast_{g}.csv")
            live = pd.read_csv(os.path.join(INTEG, f"ev_actuals_{g}.csv"), dtype={"LAD22CD": str})
            geos = set(live[col])
            self.assertEqual(set(df[col]), geos, g)
            self.assertEqual(len(df), len(geos) * 2 * len(months), g)
            self.assertFalse(df.duplicated([col, "EV_Type", "period"]).any(), g)

    def test_4_rows_are_monthly_provisional_nowcast_only(self):
        for g in GEO_COL:
            df = self.read(f"smmt_nowcast_{g}.csv")
            self.assertTrue((df["forecast_source"] == FORECAST_SOURCE).all())
            self.assertTrue((df["time_grain"] == "month").all() and (df["metric"] == "stock").all())
            self.assertTrue(set(df["EV_Type"]) == {"BEV", "PHEV"})
            self.assertTrue((df["value"] >= 0).all())
            ends = pd.PeriodIndex(df["period"], freq="M").end_time.strftime("%Y-%m-%d")
            self.assertTrue((df["period_end"].values == ends).all())
            self.assertTrue((df["period"] > self.m["baseline_period"]).all())

    def test_5_preview_is_live_plus_candidate_rows(self):
        for g, col in GEO_COL.items():
            live = pd.read_csv(os.path.join(INTEG, f"monthly_forecast_{g}.csv"), dtype=str, keep_default_na=False)
            prev = pd.read_csv(os.path.join(self.folder, "preview", f"monthly_forecast_{g}.csv"), dtype=str,
                               keep_default_na=False)        # live rows are carried over byte-for-byte as text
            n = len(live[live["forecast_source"] != FORECAST_SOURCE])
            pd.testing.assert_frame_equal(prev.iloc[:n].reset_index(drop=True),
                                          live[live["forecast_source"] != FORECAST_SOURCE].reset_index(drop=True).astype(str),
                                          check_dtype=False)
            self.assertTrue((prev.iloc[n:]["forecast_source"] == FORECAST_SOURCE).all())

    def test_6_manifest_provenance(self):
        inp = self.m["inputs"]
        for k in ("fingerprint", "c1_lsoa_sha256", "veh0135_sha256", "live_integration_sha256", "smmt_entries"):
            self.assertIn(k, inp)
        self.assertEqual({f"{e['year']}-{e['month']:02d}" for e in inp["smmt_entries"]} >= set(self.m["nowcast_months"]), True)
        self.assertFalse(self.m["published"])
        self.assertEqual(json.load(open(os.path.join(self.folder, "manifest.json")))["seal_sha256"], self.m["seal_sha256"])


if __name__ == "__main__":
    unittest.main()
