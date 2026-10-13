"""Dated annual HP forecast anchors from locked NESO files and DFES domestic heating."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .hp_config import (
    BB_TO_HP_DETAIL,
    DFES_HP_WORKBOOKS,
    DFES_SCENARIO_WORLD,
    DFES_SHEET,
    HP_DETAIL_TO_BB,
    HP_LSOA_LOOKUP,
    NESO_HP_FILES,
    TECH_TYPE_HP,
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


def _norm_world(value) -> str:
    return str(value).replace(" ", "").strip()


def _standard_hp_anchor_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["anchor_year"] = out["anchor_year"].astype(int)
    out["anchor_date"] = out["anchor_year"].map(anchor_date_for_year)
    out["financial_year"] = out["anchor_year"].map(financial_year_label)
    out["tech_type"] = TECH_TYPE_HP
    out["forecast_value"] = pd.to_numeric(out["forecast_value"], errors="coerce")
    cols = [
        "LSOA21CD",
        "anchor_year",
        "anchor_date",
        "financial_year",
        "tech_type",
        "technology_detail",
        "forecast_source",
        "forecast_scenario",
        "dfes_pathway",
        "building_block_id",
        "forecast_value",
    ]
    return out[cols]


def resolve_dfes_workbook() -> Path:
    for path in DFES_HP_WORKBOOKS:
        if path.exists():
            return path
    raise FileNotFoundError(
        "DFES domestic heating workbook not found. Tried:\n"
        + "\n".join(str(p) for p in DFES_HP_WORKBOOKS)
    )


def load_neso_hp_anchors() -> pd.DataFrame:
    frames = []
    for (scenario, bb, detail), path in NESO_HP_FILES.items():
        raw = pd.read_csv(
            path,
            usecols=["scenario", "bb", "year", "LSOA21CD", "forecast_value"],
        )
        if raw["scenario"].nunique() != 1 or raw["scenario"].iloc[0] != scenario:
            raise ValueError(f"{path} scenario mismatch: {raw['scenario'].unique()}")
        if raw["bb"].nunique() != 1 or raw["bb"].iloc[0] != bb:
            raise ValueError(f"{path} BB mismatch: {raw['bb'].unique()}")
        raw["LSOA21CD"] = raw["LSOA21CD"].astype(str).str.strip()
        raw = raw.rename(columns={"year": "anchor_year"})
        raw["technology_detail"] = detail
        raw["forecast_source"] = "NESO"
        raw["forecast_scenario"] = scenario
        raw["dfes_pathway"] = pd.NA
        raw["building_block_id"] = bb
        frames.append(raw)
    neso = pd.concat(frames, ignore_index=True)
    universe = pd.read_csv(HP_LSOA_LOOKUP, usecols=["LSOA21CD"]).drop_duplicates()
    universe["LSOA21CD"] = universe["LSOA21CD"].astype(str).str.strip()
    years = pd.DataFrame({"anchor_year": sorted(neso["anchor_year"].unique())})
    keys = neso[
        ["technology_detail", "forecast_source", "forecast_scenario", "building_block_id"]
    ].drop_duplicates()
    panel = (
        universe.assign(_k=1)
        .merge(years.assign(_k=1), on="_k")
        .merge(keys.assign(_k=1), on="_k")
        .drop(columns="_k")
    )
    neso = panel.merge(
        neso.drop(columns=["dfes_pathway"]),
        on=[
            "LSOA21CD",
            "anchor_year",
            "technology_detail",
            "forecast_source",
            "forecast_scenario",
            "building_block_id",
        ],
        how="left",
    )
    neso["dfes_pathway"] = pd.NA
    neso["forecast_value"] = neso["forecast_value"].fillna(0.0)
    return _standard_hp_anchor_frame(neso)


def load_dfes_hp_anchors() -> tuple[pd.DataFrame, pd.DataFrame, Path]:
    path = resolve_dfes_workbook()
    df = pd.read_excel(path, sheet_name=DFES_SHEET)
    year_cols = _year_columns(df.columns)
    if not year_cols:
        raise ValueError(f"No 2024-2050 year columns in {path} / {DFES_SHEET}")
    if "Scenario World" not in df.columns:
        raise ValueError(f"{path} / {DFES_SHEET} missing Scenario World")
    if "Parameter" not in df.columns:
        raise ValueError(f"{path} / {DFES_SHEET} missing Parameter")

    df = df[df["Scenario World"].map(_norm_world) == DFES_SCENARIO_WORLD].copy()
    wanted = set(BB_TO_HP_DETAIL.values())
    df = df[df["Parameter"].astype(str).str.strip().isin(wanted)].copy()
    if df.empty:
        raise ValueError(f"No DFES rows for {wanted} in {path}")

    keep = ["LSOA21CD", "Parameter", *year_cols]
    if "Scenario" in df.columns:
        keep.insert(1, "Scenario")
    long = df[keep].melt(
        id_vars=[c for c in keep if c not in year_cols],
        value_vars=year_cols,
        var_name="anchor_year",
        value_name="forecast_value",
    )
    long["LSOA21CD"] = long["LSOA21CD"].astype(str).str.strip()
    long["anchor_year"] = long["anchor_year"].astype(int)
    long["forecast_value"] = pd.to_numeric(long["forecast_value"], errors="coerce").fillna(0.0)
    long["technology_detail"] = long["Parameter"].astype(str).str.strip()
    long["building_block_id"] = long["technology_detail"].map(HP_DETAIL_TO_BB)
    long["forecast_source"] = "DFES"
    long["forecast_scenario"] = DFES_SCENARIO_WORLD
    if "Scenario" in long.columns:
        long["dfes_pathway"] = long["Scenario"].astype(str)
    else:
        long["dfes_pathway"] = "Holistic Transition"

    lineage = long.copy()
    dashboard = (
        lineage.groupby(
            ["LSOA21CD", "anchor_year", "technology_detail", "building_block_id"],
            as_index=False,
        )["forecast_value"]
        .sum()
    )
    dashboard["forecast_source"] = "DFES"
    dashboard["forecast_scenario"] = DFES_SCENARIO_WORLD
    dashboard["dfes_pathway"] = "Combined"
    return _standard_hp_anchor_frame(dashboard), lineage, path


def build_hp_annual_anchors() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Path]:
    neso = load_neso_hp_anchors()
    dfes, lineage, dfes_path = load_dfes_hp_anchors()
    anchors = pd.concat([neso, dfes], ignore_index=True)
    anchors = anchors.sort_values(
        ["forecast_source", "forecast_scenario", "technology_detail", "LSOA21CD", "anchor_year"]
    ).reset_index(drop=True)
    return anchors, neso, lineage, dfes_path
