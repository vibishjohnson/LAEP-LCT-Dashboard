"""Dataset identification, validation, reporting-period detection, UKPN scope and comparison."""

import csv
import os
from dataclasses import replace

import pytest

from data_manager.ingestion import contracts, readers
from data_manager.ingestion.config import Settings

from .conftest import EV, LOOKUP, write_ods, read_csv_rows

S = Settings()
SCOPE = contracts.UkpnScope(LOOKUP)


def run(path, fmt, sheet, contract, tmp_path=None, **kw):
    canonical = str(tmp_path / "canonical.csv") if tmp_path else None
    return contracts.validate_and_profile(readers.iter_rows(path, fmt, sheet, S), contract, SCOPE, canonical, **kw)


def heads(path, fmt):
    out = {}
    for s in readers.sheet_names(path, fmt, S):
        it = readers.iter_rows(path, fmt, s, S)
        out[s] = [r for _, r in zip(range(40), it)]
        it.close() if hasattr(it, "close") else None
    return out


def statuses(ck):
    return {c["check"]: c["status"] for c in ck.items}


# ------------------------------------------------------------------ scope lookup (CLAUDE.md: report UKPN filtering)
def test_ukpn_scope_lookup_is_epn_lpn_spn_only():
    assert SCOPE.available
    assert set(SCOPE.lsoa_dno.values()) == {"EPN", "LPN", "SPN"}
    # the approved C1 geography: 4,993 EPN + 2,931 LPN + 3,099 SPN = 11,023 LSOAs
    counts = {d: sum(1 for v in SCOPE.lsoa_dno.values() if v == d) for d in ("EPN", "LPN", "SPN")}
    assert counts == {"EPN": 4993, "LPN": 2931, "SPN": 3099}


# ------------------------------------------------------------------ identification
def test_identify_ods_tables_by_content(veh0142_ods, veh0105_ods):
    best142 = contracts.identify(heads(veh0142_ods, "ods"), "anything.ods")[0]
    best105 = contracts.identify(heads(veh0105_ods, "ods"), "anything.ods")[0]
    assert (best142["dataset"], best142["sheet"], best142["header_row"]) == ("VEH0142", "VEH0142", 4)
    assert (best105["dataset"], best105["sheet"], best105["header_row"]) == ("VEH0105", "VEH0105", 4)


def test_misleading_filename_does_not_change_identification(veh0105_ods):
    best = contracts.identify(heads(veh0105_ods, "ods"), "VEH0142_latest.ods")[0]
    assert best["dataset"] == "VEH0105"


def test_identify_csv_tables(veh0135_csv):
    best = contracts.identify(heads(veh0135_csv, "csv"), "x.csv")[0]
    assert best["dataset"] == "VEH0135" and best["header_row"] == 0


def test_unrecognised_file_has_no_candidates(tmp_path):
    p = tmp_path / "other.csv"
    p.write_text("Region,Value\nEast,1\n", encoding="utf-8")
    assert contracts.identify(heads(str(p), "csv")) == []


# ------------------------------------------------------------------ validation on realistic tables
def test_veh0142_ods_validates_and_canonical_matches_production(tmp_path, veh0142_ods):
    ck, prof, prev, sha = run(veh0142_ods, "ods", "VEH0142", contracts.CONTRACTS["VEH0142"], tmp_path)
    assert not ck.errors, ck.errors
    assert prof["latest_quarter"] == "2026 Q1" and prof["period_end"] == "2026-03-31"
    assert prof["rows"] == 25996 and prof["header_row"] == 5 and prof["title_rows"][1] == "Table VEH0142"
    assert prof["scope"]["ukpn_rows"] + prof["scope"]["non_ukpn_rows"] == prof["rows"]
    # canonical table == production CSV export, value for value (numbers without thousands separators)
    canon = read_csv_rows(str(tmp_path / "canonical.csv"))
    prod = read_csv_rows(os.path.join(EV, "VEH0142.csv"))
    assert canon[0] == prod[0] and len(canon) == len(prod)
    for a, b in zip(canon[1:], prod[1:]):
        assert a[:7] == b[:7]                                # identifiers, incl. " County Durham" leading space
        assert a[7:] == [v.replace(",", "") for v in b[7:]]
    assert sha == contracts._file_sha256(str(tmp_path / "canonical.csv"))


def test_wrong_classification_is_caught_by_units(tmp_path, veh0105_ods):
    """A VEH0105 workbook forced through the VEH0142 contract fails (Units, Fuel), not silently accepted."""
    c142 = contracts.CONTRACTS["VEH0142"]
    ck, prof, _, _ = run(veh0105_ods, "ods", "VEH0105", c142)
    st = statuses(ck)
    assert st["units"] == "FAIL" and st["required_fuel_values"] == "FAIL"


def test_veh0135_real_structure_validates(veh0135_csv):
    ck, prof, prev, _ = run(veh0135_csv, "csv", None, contracts.CONTRACTS["VEH0135"])
    assert not ck.errors, ck.errors
    assert prof["latest_quarter"] == "2026 Q1" and len(prof["quarters"]) == 58
    assert prof["placeholder_geo_rows"] == 40                        # "Miscellaneous" rows are accepted, not dropped
    sc = prof["scope"]
    assert sc["total_rows"] == 293825 and sc["ukpn_rows"] + sc["non_ukpn_rows"] == 293825
    assert "LSOA21CD" in sc["field"]
    # C1 inputs: national Total-keepership BEV+PHEV and UKPN by DNO for 2026 Q1
    s = prof["series"]
    ukpn_total = sum(s[f"{d}|{f}_Total"]["2026 Q1"] for d in ("EPN", "LPN", "SPN") for f in ("BEV", "PHEV"))
    assert ukpn_total > 0 and s["ALL|BEV_Total"]["2026 Q1"] > ukpn_total


def _small_0135(tmp_path, rows):
    p = tmp_path / "small.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    return str(p)


@pytest.fixture
def small_contract():
    return replace(contracts.CONTRACTS["VEH0135"], min_rows=1, min_quarters=2)


def _rows(n=6):
    hdr = ["LSOA21CD", "LSOA21NM", "Fuel", "Keepership", "2026 Q2", "2026 Q1", "2025 Q4"]
    body = []
    for i, (fuel, keep) in enumerate([(f, k) for f in ("Battery electric", "Plug-in hybrid electric (petrol)", "Plug-in hybrid electric (diesel)")
                                      for k in ("Total", "Private", "Company")]):
        body.append([f"E0100000{i % 9 + 1}", "x", fuel, keep, "10", "9", "[c]"])
    return [hdr] + body


def test_duplicate_keys_fail(tmp_path, small_contract):
    rows = _rows() + [_rows()[1]]
    ck, *_ = run(_small_0135(tmp_path, rows), "csv", None, small_contract)
    assert statuses(ck)["unique_keys"] == "FAIL"


def test_bad_geography_and_numbers_fail(tmp_path, small_contract):
    rows = _rows()
    rows[1][0] = "E1000001"            # malformed LSOA code
    rows[2][4] = "12a"                 # not a number or DfT marker
    rows[3][5] = "-4"
    ck, *_ = run(_small_0135(tmp_path, rows), "csv", None, small_contract)
    st = statuses(ck)
    assert st["geography_codes"] == "FAIL" and st["numeric_cells"] == "FAIL"


def test_empty_latest_quarter_is_incomplete(tmp_path, small_contract):
    rows = _rows()
    for r in rows[1:]:
        r[4] = ""
    ck, *_ = run(_small_0135(tmp_path, rows), "csv", None, small_contract)
    assert statuses(ck)["latest_quarter_populated"] == "FAIL"


def test_quarter_gap_and_order_fail(tmp_path, small_contract):
    rows = _rows()
    rows[0][4:] = ["2026 Q2", "2025 Q4", "2026 Q1"]
    ck, *_ = run(_small_0135(tmp_path, rows), "csv", None, small_contract)
    assert statuses(ck)["quarter_sequence"] == "FAIL"


def test_missing_c1_fuel_category_fails(tmp_path, small_contract):
    rows = [r for r in _rows() if r[2] != "Plug-in hybrid electric (petrol)"]
    ck, *_ = run(_small_0135(tmp_path, rows), "csv", None, small_contract)
    assert statuses(ck)["required_fuel_values"] == "FAIL"


def test_missing_header_fails_cleanly(tmp_path, small_contract):
    p = _small_0135(tmp_path, [["something else"], ["1"]])
    ck, prof, prev, sha = run(p, "csv", None, small_contract)
    assert statuses(ck) == {"header_found": "FAIL"} and prof == {} and sha is None


def test_reporting_period_detection():
    assert contracts.quarter_end("2026 Q2") == "2026-06-30"
    assert contracts.quarter_end("2025 Q4") == "2025-12-31"
    assert contracts.quarter_index("2026 Q1") - contracts.quarter_index("2025 Q4") == 1


def test_mixed_numeric_text_and_missing_cells_in_ods(tmp_path):
    """Title rows, blank cells, markers and floats in one DfT-shaped sheet."""
    c = replace(contracts.CONTRACTS["VEH0105"], min_rows=1, min_quarters=2, required_values={})
    hdr = list(c.id_columns) + ["2026 Q2", "2026 Q1"]
    data = [["Thousands", "Cars", "Petrol", "Total", "1", "K02000001", "United Kingdom", 108.769, 109.441],
            ["Thousands", "Cars", "Petrol", "Total", "2", "E09000033", "  Westminster", 0.625, "[x]"],
            ["Thousands", "Cars", "Petrol", "Total", "3", "[z]", "Region or county unknown", "", 11.471]]
    p = write_ods(str(tmp_path / "m.ods"), {"VEH0105": [["Title"], ["Table VEH0105"], [], hdr] + data})
    ck, prof, prev, _ = run(p, "ods", "VEH0105", c)
    assert not ck.errors, ck.errors
    assert prev["rows"][0][7:] == ["108.769", "109.441"]
    assert prev["rows"][1][6] == "  Westminster" and prev["rows"][1][7:] == ["0.625", "[x]"]
    assert prev["rows"][2][5] == "[z]" and prev["rows"][2][7] == ""
    assert prof["placeholder_geo_rows"] == 1 and prof["marker_cells"] == 1
    assert prof["scope"]["ukpn_rows"] == 1                      # Westminster (LPN); UK and unknown rows are outside UKPN


# ------------------------------------------------------------------ comparison
def test_comparison_flags_restatement_rows_and_categories():
    base = {"latest_quarter": "2026 Q1", "rows": 100, "categories": {"Fuel": {"A": 50, "B": 50}},
            "series": {"ALL|BEV_Total": {"2026 Q1": 1000.0, "2025 Q4": 900.0}}}
    cand = {"latest_quarter": "2026 Q2", "rows": 90, "categories": {"Fuel": {"A": 50, "C": 40}},
            "series": {"ALL|BEV_Total": {"2026 Q2": 1100.0, "2026 Q1": 1030.0}}}
    cmp = contracts.compare_profiles(cand, base)
    assert cmp["quarter_relation"] == "NEWER" and cmp["rows"]["change_pct"] == -10.0
    s = cmp["series"][0]
    assert s["restatement_pct"] == 3.0 and s["growth_pct"] == pytest.approx(6.796, abs=1e-3)
    flags = contracts.comparison_warnings(cmp)
    assert any("row count" in f for f in flags) and any("restated" in f for f in flags) and any("categories" in f for f in flags)
    older = contracts.compare_profiles({**cand, "latest_quarter": "2025 Q4"}, base)
    assert older["quarter_relation"] == "OLDER" and any("older" in f for f in contracts.comparison_warnings(older))
