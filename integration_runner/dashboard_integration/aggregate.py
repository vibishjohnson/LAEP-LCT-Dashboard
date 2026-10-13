"""Monthly interpolation then geography aggregation. No LSOA-month CSV."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from .interpolate import month_ends_inclusive


def _as_date(value) -> date:
    if isinstance(value, date) and not isinstance(value, pd.Timestamp):
        return value
    return pd.to_datetime(value).date()


def _wide_from_anchors(group: pd.DataFrame) -> pd.DataFrame:
    wide = group.pivot_table(
        index="LSOA21CD",
        columns="anchor_date",
        values="forecast_value",
        aggfunc="sum",
    )
    wide.columns = [_as_date(c) for c in wide.columns]
    return wide.sort_index(axis=1)


def _monthly_matrix(wide: pd.DataFrame) -> tuple[list[date], np.ndarray]:
    dates = list(wide.columns)
    months = month_ends_inclusive(dates[0], dates[-1])
    index = {t: i for i, t in enumerate(months)}
    values = np.zeros((len(wide), len(months)), dtype=np.float64)

    for i, t0 in enumerate(dates[:-1]):
        t1 = dates[i + 1]
        v0 = wide[t0].to_numpy(dtype=np.float64)
        v1 = wide[t1].to_numpy(dtype=np.float64)
        span = (t1 - t0).days
        last_interval = i == len(dates) - 2
        for t in month_ends_inclusive(t0, t1):
            if t == t1 and not last_interval:
                continue
            frac = 0.0 if span == 0 else (t - t0).days / span
            values[:, index[t]] = v0 + (v1 - v0) * frac

    # Guarantee exact anchors.
    for t0 in dates:
        values[:, index[t0]] = wide[t0].to_numpy(dtype=np.float64)
    return months, values


def interpolate_and_aggregate(
    anchors: pd.DataFrame,
    geography: pd.DataFrame,
    *,
    tech_type: str = "EV",
    subtype_col: str = "EV_Type",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    geo = geography[["LSOA21CD", "LAD22CD", "LAD22NM", "LAEP", "DNO"]].drop_duplicates()
    geo = geo.set_index("LSOA21CD")

    dno_rows = []
    la_rows = []
    laep_rows = []
    coverage = []
    neso_anchor_checks = []

    keys = ["forecast_source", "forecast_scenario", subtype_col, "dfes_pathway", "building_block_id"]
    grouped = anchors.groupby(keys, dropna=False)

    for key, group in grouped:
        source, scenario, subtype, pathway, bb = key
        print(f"  interpolating {tech_type} {source} {scenario} {subtype}...", flush=True)
        wide = _wide_from_anchors(group)
        months, matrix = _monthly_matrix(wide)
        aligned = geo.reindex(wide.index)
        if source == "NESO" and (aligned["DNO"].isna().any() or aligned["LAD22CD"].isna().any()):
            raise ValueError(f"NESO monthly rows missing DNO/LAD for {key}")

        annual = group[["LSOA21CD", "anchor_year", "anchor_date", "forecast_value"]].copy()
        annual["anchor_date"] = annual["anchor_date"].map(_as_date)
        month_index = {t: i for i, t in enumerate(months)}
        if source == "NESO":
            # Vectorised LSOA-year anchor check
            annual_wide = annual.pivot(index="LSOA21CD", columns="anchor_date", values="forecast_value")
            annual_wide = annual_wide.reindex(wide.index)
            diffs = []
            for t0 in annual_wide.columns:
                t0d = _as_date(t0)
                monthly_vals = pd.Series(matrix[:, month_index[t0d]], index=wide.index, name="monthly_value")
                file_vals = annual_wide[t0].astype(float)
                part = pd.DataFrame({
                    "LSOA21CD": wide.index,
                    "anchor_date": t0d,
                    "forecast_value": file_vals.to_numpy(),
                    "monthly_value": monthly_vals.to_numpy(),
                })
                part["abs_diff"] = (part["forecast_value"] - part["monthly_value"]).abs()
                part["forecast_source"] = source
                part["forecast_scenario"] = scenario
                part[subtype_col] = subtype
                part["building_block_id"] = bb
                diffs.append(part)
            neso_anchor_checks.append(pd.concat(diffs, ignore_index=True))

        meta = {
            "time_grain": "month",
            "tech_type": tech_type,
            subtype_col: subtype,
            "forecast_source": source,
            "forecast_scenario": scenario,
            "dfes_pathway": None if pd.isna(pathway) else pathway,
            "building_block_id": None if pd.isna(bb) else bb,
            "metric": "stock",
            "observation_source": source,
        }
        date_set = set(wide.columns)

        for j, t in enumerate(months):
            stock = pd.Series(matrix[:, j], index=wide.index)
            complete = aligned.copy()
            complete["forecast_value"] = stock
            complete = complete[complete["DNO"].notna() & complete["LAD22CD"].notna()]

            left_dates = [d for d in wide.columns if d <= t]
            source_year = (left_dates[-1].year - 1) if left_dates else t.year - 1
            left_anchor = left_dates[-1] if left_dates else t
            row_meta = {
                **meta,
                "period": t.strftime("%Y-%m"),
                "period_end": t,
                "is_anchor": t in date_set,
                "is_interpolated": t not in date_set,
                "source_year": source_year,
                "anchor_date": left_anchor,
            }

            dno = complete.groupby("DNO", dropna=False)["forecast_value"].sum().reset_index()
            for col, val in row_meta.items():
                dno[col] = val
            dno_rows.append(dno)

            la = complete.groupby(["LAD22CD", "LAD22NM"], dropna=False)["forecast_value"].sum().reset_index()
            for col, val in row_meta.items():
                la[col] = val
            la_rows.append(la)

            laep_mapped = complete[complete["LAEP"].notna()]
            laep = laep_mapped.groupby("LAEP", dropna=False)["forecast_value"].sum().reset_index()
            for col, val in row_meta.items():
                laep[col] = val
            laep_rows.append(laep)

            coverage.append({
                "forecast_source": source,
                "forecast_scenario": scenario,
                subtype_col: subtype,
                "period": t.strftime("%Y-%m"),
                "lsoa_total": float(complete["forecast_value"].sum()),
                "la_total": float(la["forecast_value"].sum()) if not la.empty else 0.0,
                "dno_total": float(dno["forecast_value"].sum()) if not dno.empty else 0.0,
                "laep_total": float(laep["forecast_value"].sum()) if not laep.empty else 0.0,
                "n_lsoa": int(complete.index.nunique()),
                "n_lsoa_with_laep": int(laep_mapped.index.nunique()),
            })

    monthly_dno = pd.concat(dno_rows, ignore_index=True)
    monthly_la = pd.concat(la_rows, ignore_index=True)
    monthly_laep = pd.concat(laep_rows, ignore_index=True)
    coverage_df = pd.DataFrame(coverage)
    neso_anchor_df = pd.concat(neso_anchor_checks, ignore_index=True) if neso_anchor_checks else pd.DataFrame()
    return monthly_dno, monthly_la, monthly_laep, coverage_df, neso_anchor_df
