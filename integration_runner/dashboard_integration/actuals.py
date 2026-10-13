"""EV actuals: genuine quarter-end observations only."""

from __future__ import annotations

import pandas as pd

from .config import EV_ACTUAL_FROM_PERIOD, EV_QUARTERLY, UKPN_DNOS


def _quarter_end(period: str) -> str:
    year, month = period.split("-")
    last = {"03": "31", "06": "30", "09": "30", "12": "31"}[month]
    return f"{year}-{month}-{last}"


def load_ev_actuals(geography: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, str]:
    raw = pd.read_csv(EV_QUARTERLY, usecols=["period", "LSOA21CD", "DNO", "install_count", "EV_Type"])
    raw = raw[raw["DNO"].isin(UKPN_DNOS)].copy()
    raw = raw[raw["period"] >= EV_ACTUAL_FROM_PERIOD].copy()
    raw["install_count"] = pd.to_numeric(raw["install_count"], errors="coerce").fillna(0.0)
    if raw.empty:
        raise ValueError("No EV actuals found in the expected window")

    latest = str(sorted(raw["period"].unique())[-1])

    geo = geography[["LSOA21CD", "LAD22CD", "LAD22NM", "LAEP", "DNO"]].drop_duplicates()
    # Prefer pipeline DNO; fill geography for the 12 dashboard-only LSOAs already present.
    mapped = raw.merge(geo.drop(columns=["DNO"]), on="LSOA21CD", how="left")

    def _stamp(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["period_end"] = out["period"].map(_quarter_end)
        out["time_grain"] = "quarter"
        out["tech_type"] = "EV"
        out["observation_source"] = "ACTUAL"
        out["forecast_source"] = pd.NA
        out["forecast_scenario"] = pd.NA
        out["dfes_pathway"] = pd.NA
        out["building_block_id"] = pd.NA
        out["metric"] = "stock"
        out["is_interpolated"] = False
        out["is_anchor"] = False
        out["source_year"] = pd.NA
        out["anchor_date"] = pd.NA
        out = out.rename(columns={"install_count": "value"})
        return out

    dno = mapped.groupby(["period", "DNO", "EV_Type"], as_index=False)["install_count"].sum()
    la = mapped.dropna(subset=["LAD22CD"]).groupby(
        ["period", "LAD22CD", "LAD22NM", "EV_Type"], as_index=False
    )["install_count"].sum()
    laep = mapped.dropna(subset=["LAEP"]).groupby(
        ["period", "LAEP", "EV_Type"], as_index=False
    )["install_count"].sum()

    return _stamp(dno), _stamp(la), _stamp(laep), latest
