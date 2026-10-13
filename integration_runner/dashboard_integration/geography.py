"""Geography contract: 11,077 HT LSOAs plus 12 actuals-only dashboard LSOAs."""

from __future__ import annotations

import pandas as pd

from .config import (
    DASHBOARD_ONLY_COUNT,
    HT_LSOA_LOOKUP,
    HT_ONLY_COUNT,
    LA_LAEP,
    LSOA_LAD_ONS,
    LSOA_TO_DNO,
    NESO_LSOA_COUNT,
    UKPN_DNOS,
)


def _load_dno_lookup() -> pd.DataFrame:
    dno = pd.read_csv(LSOA_TO_DNO, encoding="utf-8-sig")
    dno = dno[
        ["LSOA21CD", "LSOA21NM", "LAD22CD", "LAD22NM", "Majority Licence area"]
    ].drop_duplicates()
    dno = dno.rename(columns={"Majority Licence area": "DNO"})
    dno = dno[dno["DNO"].isin(UKPN_DNOS)].copy()
    return dno


def _load_ons_lad() -> pd.DataFrame:
    ons = pd.read_csv(LSOA_LAD_ONS, encoding="utf-8-sig", usecols=["LSOA21CD", "LAD22CD", "LAD22NM"])
    return ons.drop_duplicates("LSOA21CD")


def _load_laep() -> pd.DataFrame:
    laep = pd.read_csv(LA_LAEP, encoding="utf-8-sig")
    laep = laep[["ID_CODE", "NAME", "LAEP"]].drop_duplicates("ID_CODE")
    laep = laep.rename(columns={"ID_CODE": "LAD22CD", "NAME": "LA_NAME"})
    return laep


def build_geography_lookup() -> pd.DataFrame:
    ht = pd.read_csv(
        HT_LSOA_LOOKUP,
        usecols=["LSOA21CD", "canonical_spatial_gsp", "LSOA21NM"],
    ).drop_duplicates("LSOA21CD")
    if ht["LSOA21CD"].nunique() != NESO_LSOA_COUNT:
        raise ValueError(f"HT lookup has {ht['LSOA21CD'].nunique()} LSOAs, expected {NESO_LSOA_COUNT}")

    dno = _load_dno_lookup()
    ons = _load_ons_lad()
    laep = _load_laep()

    geo = ht.merge(dno[["LSOA21CD", "LAD22CD", "LAD22NM", "DNO"]], on="LSOA21CD", how="left")

    missing_lad = geo["LAD22CD"].isna()
    if missing_lad.any():
        geo = geo.merge(ons, on="LSOA21CD", how="left", suffixes=("", "_ons"))
        geo.loc[missing_lad, "LAD22CD"] = geo.loc[missing_lad, "LAD22CD_ons"]
        geo.loc[missing_lad, "LAD22NM"] = geo.loc[missing_lad, "LAD22NM_ons"]
        geo = geo.drop(columns=[c for c in geo.columns if c.endswith("_ons")])

    # Infer DNO for HT-only LSOAs from the modal DNO of their spatial GSP.
    gsp_dno = (
        geo[geo["DNO"].notna()]
        .groupby("canonical_spatial_gsp")["DNO"]
        .agg(lambda s: s.value_counts().index[0])
        .to_dict()
    )
    need_dno = geo["DNO"].isna()
    geo.loc[need_dno, "DNO"] = geo.loc[need_dno, "canonical_spatial_gsp"].map(gsp_dno)

    geo = geo.merge(laep[["LAD22CD", "LAEP"]], on="LAD22CD", how="left")

    ht_set = set(ht["LSOA21CD"])
    dash_set = set(dno["LSOA21CD"])
    ht_only = ht_set - dash_set
    dash_only_ids = sorted(dash_set - ht_set)
    if len(ht_only) != HT_ONLY_COUNT:
        raise ValueError(f"Expected {HT_ONLY_COUNT} HT-only LSOAs, found {len(ht_only)}")
    if len(dash_only_ids) != DASHBOARD_ONLY_COUNT:
        raise ValueError(f"Expected {DASHBOARD_ONLY_COUNT} dashboard-only LSOAs, found {len(dash_only_ids)}")

    geo["in_neso_spatial"] = True
    geo["in_legacy_dashboard_lookup"] = geo["LSOA21CD"].isin(dash_set)
    geo["geography_flag"] = "NESO_SPATIAL"
    geo.loc[geo["LSOA21CD"].isin(ht_only), "geography_flag"] = "HT_ONLY_BOUNDARY"

    extra = dno[dno["LSOA21CD"].isin(dash_only_ids)].copy()
    extra = extra.merge(laep[["LAD22CD", "LAEP"]], on="LAD22CD", how="left")
    extra["canonical_spatial_gsp"] = pd.NA
    extra["in_neso_spatial"] = False
    extra["in_legacy_dashboard_lookup"] = True
    extra["geography_flag"] = "NESO_SPATIAL_NOT_ASSIGNED"

    cols = [
        "LSOA21CD",
        "LSOA21NM",
        "LAD22CD",
        "LAD22NM",
        "LAEP",
        "DNO",
        "canonical_spatial_gsp",
        "in_neso_spatial",
        "in_legacy_dashboard_lookup",
        "geography_flag",
    ]
    out = pd.concat([geo[cols], extra[cols]], ignore_index=True)
    out = out.sort_values(["geography_flag", "LSOA21CD"]).reset_index(drop=True)

    neso = out[out["in_neso_spatial"]]
    if neso["LSOA21CD"].nunique() != NESO_LSOA_COUNT:
        raise ValueError("NESO spatial universe lost during geography construction")
    if neso["DNO"].isna().any():
        missing = neso[neso["DNO"].isna()]["LSOA21CD"].tolist()
        raise ValueError(f"NESO LSOAs without DNO: {missing[:10]}")
    if neso["LAD22CD"].isna().any():
        missing = neso[neso["LAD22CD"].isna()]["LSOA21CD"].tolist()
        raise ValueError(f"NESO LSOAs without LAD: {missing[:10]}")
    return out
