"""
Geography of the DfT body-type file written by scripts/01_ev_processing.py (project/output_processed/lsoa_ev_bodytype.csv).

VEH0135 is on LSOA 2021 geography and must stay on it: no duplicated keys, merged and split 2011/2021 areas kept
separately, one LAD per LSOA, totals conserved, and the C1 fuel definition. These tests fail on the pre-fix outputs.
Run from the repository root: python -m pytest tests/test_ev_bodytype_geography.py -q -p no:cacheprovider
"""
import hashlib
import importlib.util
import json
import os

import pandas as pd
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "project", "output_processed")
BRIDGE = os.path.join(REPO, "lookups", "LSOA_(2011)_to_LSOA_(2021)_to_Local_Authority_District_(2022)_Exact_Fit_Lookup_for_EW_(V3).csv")
sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()


@pytest.fixture(scope="module", autouse=True)
def outputs_untouched():
    files = [os.path.join(OUT, n) for n in ("lsoa_ev_bodytype.csv", "ev_missing_geo.csv", "ev_geography_qa.json")]
    before = {p: sha(p) for p in files}
    yield
    assert {p: sha(p) for p in files} == before


# ---------------------------------------------------------------- body-type geography (scripts/01_ev_processing.py)
@pytest.fixture(scope="module")
def bodytype():
    return pd.read_csv(os.path.join(OUT, "lsoa_ev_bodytype.csv"), dtype={"LSOA11CD": str, "LSOA21CD": str, "LAD22CD": str})


@pytest.fixture(scope="module")
def bridge():
    b = pd.read_csv(BRIDGE, dtype=str)
    return b[["LSOA11CD", "LSOA21CD", "LAD22CD"]].drop_duplicates()


def test_bodytype_keys_unique(bodytype):
    assert not bodytype.duplicated(["period", "LSOA21CD", "Fuel", "BodyType"]).any()


def test_bodytype_one_lad_per_lsoa21_matching_bridge(bodytype, bridge):
    lad = bridge.groupby("LSOA21CD")["LAD22CD"].first()
    g = bodytype.groupby("LSOA21CD")["LAD22CD"].nunique()
    assert (g == 1).all()
    assert (bodytype.drop_duplicates("LSOA21CD").set_index("LSOA21CD")["LAD22CD"] == lad.reindex(g.index)).all()


def test_bodytype_split_and_merged_areas_kept_separately(bodytype, bridge):
    n21 = bridge.groupby("LSOA11CD")["LSOA21CD"].nunique()
    split_children = set(bridge[bridge["LSOA11CD"].isin(n21[n21 > 1].index)]["LSOA21CD"])
    n11 = bridge.groupby("LSOA21CD")["LSOA11CD"].nunique()
    merged = set(n11[n11 > 1].index)
    march = bodytype[bodytype["period"] == "2026-03-31"]
    present = set(march["LSOA21CD"])
    missing = pd.read_csv(os.path.join(OUT, "ev_missing_geo.csv"), dtype={"LSOA21CD": str}, usecols=["period", "LSOA21CD", "BodyType"])
    unalloc = set(missing[(missing["BodyType"] == "UNALLOCATED") & (missing["period"] == "2026-03-31")]["LSOA21CD"])
    # LSOAs with BEV/PHEV stock in the source (some areas only have a 'Total' or non-plug-in fuel line in VEH0135)
    src = pd.read_csv(os.path.join(REPO, "ev", "df_VEH0135.csv"), usecols=["LSOA21CD", "Fuel", "Keepership", "2026 Q1"], dtype=str)
    src = src[(src["Keepership"] == "Total") & src["Fuel"].isin(["Battery electric", "Plug-in hybrid electric (petrol)",
                                                                  "Plug-in hybrid electric (diesel)"])]
    v = pd.to_numeric(src["2026 Q1"].str.replace(",", "", regex=False), errors="coerce").fillna(0)
    with_stock = set(src.loc[v > 0, "LSOA21CD"])
    # every split child and merged area with stock is present on its own (or explicitly unallocated: no LAD proportions)
    assert len(split_children & with_stock) > 1000 and len(merged & with_stock) > 100
    assert not ((split_children & with_stock) - present - unalloc)
    assert not ((merged & with_stock) - present - unalloc)
    assert march[march["LSOA21CD"].isin(merged)].groupby(["LSOA21CD", "Fuel", "BodyType"]).size().max() == 1


def test_bodytype_geography_qa_record():
    qa = json.load(open(os.path.join(OUT, "ev_geography_qa.json"), encoding="utf-8"))
    assert qa["duplicate_keys"] == 0 and qa["conserved"] and qa["lsoa21_lost"] == []


def test_bodytype_fuel_definition_matches_c1():
    spec = importlib.util.spec_from_file_location("ev01", os.path.join(REPO, "scripts", "01_ev_processing.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    got = m.map_fuel_to_ev(pd.Series(["Battery electric", "Plug-in hybrid electric (petrol)", "Plug-in hybrid electric (diesel)",
                                      "Hybrid electric (petrol)", "Hybrid electric (diesel)", "Range extended electric", "Petrol"]))
    assert got.tolist()[:3] == ["BEV", "PHEV", "PHEV"] and got.iloc[3:].isna().all()


def test_bodytype_no_unallocated_stock_in_ukpn():
    dno = pd.read_csv(os.path.join(REPO, "lookups", "LSOA to DNO.csv"), dtype=str)[["LSOA21CD", "Majority Licence area"]]
    missing = pd.read_csv(os.path.join(OUT, "ev_missing_geo.csv"), dtype={"LSOA21CD": str}, usecols=["LSOA21CD", "BodyType", "ev_count"])
    un = missing[missing["BodyType"] == "UNALLOCATED"].merge(dno, on="LSOA21CD")
    assert un[un["Majority Licence area"].isin(["EPN", "LPN", "SPN"])]["ev_count"].sum() == 0
