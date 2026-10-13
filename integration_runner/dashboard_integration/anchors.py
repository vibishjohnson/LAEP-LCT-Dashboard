"""Build dated annual forecast anchors from locked NESO HT files and DFES workbooks."""

from __future__ import annotations

import pandas as pd

from .config import (
    DFES_BEV_PARAMS,
    DFES_CARS_VANS,
    DFES_MOTO,
    DFES_PHEV_PARAMS,
    DFES_SCENARIO_WORLD,
    DFES_TAXI_PHV,
    HT_LSOA_LOOKUP,
    NESO_FILES,
    TECH_TYPE,
)
from .interpolate import anchor_date_for_year, financial_year_label


def _year_columns(columns) -> list:
    years = []
    for col in columns:
        try:
            year = int(col)
        except (TypeError, ValueError):
            continue
        if 2024 <= year <= 2050:
            years.append(col)
    return sorted(years, key=lambda c: int(c))


def _standard_anchor_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["anchor_year"] = out["anchor_year"].astype(int)
    out["anchor_date"] = out["anchor_year"].map(anchor_date_for_year)
    out["financial_year"] = out["anchor_year"].map(financial_year_label)
    out["tech_type"] = TECH_TYPE
    out["forecast_value"] = pd.to_numeric(out["forecast_value"], errors="coerce")
    cols = [
        "LSOA21CD",
        "anchor_year",
        "anchor_date",
        "financial_year",
        "tech_type",
        "EV_Type",
        "forecast_source",
        "forecast_scenario",
        "dfes_pathway",
        "building_block_id",
        "forecast_value",
    ]
    return out[cols]


def load_neso_anchors() -> pd.DataFrame:
    frames = []
    for (scenario, bb, ev_type), path in NESO_FILES.items():
        raw = pd.read_csv(path, usecols=["scenario", "bb", "year", "LSOA21CD", "forecast_value"])
        if raw["scenario"].nunique() != 1 or raw["scenario"].iloc[0] != scenario:
            raise ValueError(f"{path} scenario mismatch: {raw['scenario'].unique()}")
        if raw["bb"].nunique() != 1 or raw["bb"].iloc[0] != bb:
            raise ValueError(f"{path} BB mismatch: {raw['bb'].unique()}")
        raw = raw.rename(columns={"year": "anchor_year"})
        raw["EV_Type"] = ev_type
        raw["forecast_source"] = "NESO"
        raw["forecast_scenario"] = scenario
        raw["dfes_pathway"] = pd.NA
        raw["building_block_id"] = bb
        frames.append(raw)
    neso = pd.concat(frames, ignore_index=True)
    universe = pd.read_csv(HT_LSOA_LOOKUP, usecols=["LSOA21CD"]).drop_duplicates()
    years = pd.DataFrame({"anchor_year": sorted(neso["anchor_year"].unique())})
    keys = neso[["EV_Type", "forecast_source", "forecast_scenario", "building_block_id"]].drop_duplicates()
    panel = (
        universe.assign(_k=1)
        .merge(years.assign(_k=1), on="_k")
        .merge(keys.assign(_k=1), on="_k")
        .drop(columns="_k")
    )
    neso = panel.merge(
        neso.drop(columns=["dfes_pathway"]),
        on=["LSOA21CD", "anchor_year", "EV_Type", "forecast_source", "forecast_scenario", "building_block_id"],
        how="left",
    )
    neso["dfes_pathway"] = pd.NA
    neso["forecast_value"] = neso["forecast_value"].fillna(0.0)
    return _standard_anchor_frame(neso)


def _read_dfes_sheet(path, sheet_name: str, parameters: dict[str, str]) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=sheet_name)
    year_cols = _year_columns(df.columns)
    if not year_cols:
        raise ValueError(f"No 2024-2050 year columns in {path} / {sheet_name}")
    if "Scenario World" not in df.columns:
        raise ValueError(f"{path} / {sheet_name} missing Scenario World")
    df = df[df["Scenario World"].astype(str).str.replace(" ", "") == DFES_SCENARIO_WORLD].copy()
    df = df[df["Parameter"].isin(parameters)].copy()
    keep = ["LSOA21CD", "Scenario", "Parameter", *year_cols]
    df = df[keep]
    long = df.melt(
        id_vars=["LSOA21CD", "Scenario", "Parameter"],
        value_vars=year_cols,
        var_name="anchor_year",
        value_name="forecast_value",
    )
    long["anchor_year"] = long["anchor_year"].astype(int)
    long["forecast_value"] = pd.to_numeric(long["forecast_value"], errors="coerce").fillna(0.0)
    long["vehicle_type"] = long["Parameter"].map(parameters)
    return long


def load_dfes_anchors() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (dashboard-facing BEV/PHEV anchors, vehicle-type lineage)."""
    parts = [
        _read_dfes_sheet(DFES_CARS_VANS, "cars", {k: v for k, v in {**DFES_BEV_PARAMS, **DFES_PHEV_PARAMS}.items() if "Cars" in k}),
        _read_dfes_sheet(DFES_CARS_VANS, "vans", {k: v for k, v in {**DFES_BEV_PARAMS, **DFES_PHEV_PARAMS}.items() if "Vans" in k}),
        _read_dfes_sheet(DFES_TAXI_PHV, "Taxi", {"EV Taxis": "Taxis (BEV)"}),
        _read_dfes_sheet(DFES_TAXI_PHV, "PHV", {"EV PHVs": "PHVs (BEV)"}),
        _read_dfes_sheet(DFES_MOTO, "motorcycles", {"EV Motorcycles": "Motorcycles (BEV)"}),
    ]
    lineage = pd.concat(parts, ignore_index=True)
    lineage["EV_Type"] = lineage["vehicle_type"].str.contains(r"\(BEV\)", regex=True).map({True: "BEV", False: "PHEV"})
    lineage["forecast_source"] = "DFES"
    lineage["forecast_scenario"] = DFES_SCENARIO_WORLD
    lineage["dfes_pathway"] = lineage["Scenario"]
    lineage["building_block_id"] = pd.NA

    dashboard = (
        lineage.groupby(["LSOA21CD", "anchor_year", "EV_Type"], as_index=False)["forecast_value"]
        .sum()
    )
    dashboard["forecast_source"] = "DFES"
    dashboard["forecast_scenario"] = DFES_SCENARIO_WORLD
    dashboard["dfes_pathway"] = "Combined"
    dashboard["building_block_id"] = pd.NA
    return _standard_anchor_frame(dashboard), lineage


def build_annual_anchors() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    neso = load_neso_anchors()
    dfes, lineage = load_dfes_anchors()
    anchors = pd.concat([neso, dfes], ignore_index=True)
    anchors = anchors.sort_values(
        ["forecast_source", "forecast_scenario", "EV_Type", "LSOA21CD", "anchor_year"]
    ).reset_index(drop=True)
    return anchors, neso, lineage
