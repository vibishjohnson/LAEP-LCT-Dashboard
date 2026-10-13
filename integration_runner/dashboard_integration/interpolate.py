"""Elapsed-day interpolation between dated annual stock anchors."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .config import TIME_GRAIN_MONTH


def anchor_date_for_year(year: int) -> date:
    """NESO/DFES year Y → 31 March Y+1."""
    return date(int(year) + 1, 3, 31)


def financial_year_label(year: int) -> str:
    year = int(year)
    return f"{year}/{year + 1}"


def month_end(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def month_ends_inclusive(start: date, end: date) -> list[date]:
    """All calendar month-end dates from start through end inclusive.

    start and end must themselves be month-end dates.
    """
    out = []
    year, month = start.year, start.month
    while True:
        current = month_end(year, month)
        if current > end:
            break
        out.append(current)
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1
    return out


def interpolate_stock(t: date, t0: date, v0: float, t1: date, v1: float) -> float:
    """Linear interpolation in elapsed days. Exact at the endpoints."""
    if t == t0:
        return float(v0)
    if t == t1:
        return float(v1)
    span = (t1 - t0).days
    if span == 0:
        return float(v0)
    return float(v0) + (float(v1) - float(v0)) * ((t - t0).days / span)


def interpolate_wide_interval(
    v0: pd.Series,
    v1: pd.Series,
    t0: date,
    t1: date,
    month_ends: list[date],
) -> dict[date, pd.Series]:
    """Vectorised interpolation for one interval across all LSOAs."""
    span = (t1 - t0).days
    result = {}
    for t in month_ends:
        if t == t0:
            result[t] = v0.astype(float)
        elif t == t1:
            result[t] = v1.astype(float)
        else:
            frac = (t - t0).days / span
            result[t] = v0.astype(float) + (v1.astype(float) - v0.astype(float)) * frac
    return result


def monthly_frame_from_wide(
    wide: pd.DataFrame,
    *,
    forecast_source: str,
    forecast_scenario: str,
    ev_type: str,
    building_block_id: str | None,
    dfes_pathway: str | None,
) -> pd.DataFrame:
    """
    wide: index=LSOA21CD, columns=anchor_date (datetime.date), values=stock.
    Returns long monthly rows for those LSOAs. Does not write a dashboard CSV.
    """
    dates = sorted(wide.columns)
    if len(dates) < 1:
        return pd.DataFrame()

    rows = []
    for i, t0 in enumerate(dates):
        if i == len(dates) - 1:
            month_list = [t0]
            v0 = wide[t0]
            v1 = v0
            t1 = t0
            left_year = t0.year - 1
        else:
            t1 = dates[i + 1]
            v0 = wide[t0]
            v1 = wide[t1]
            # Include t0; exclude t1 so the next interval owns the next anchor.
            month_list = [t for t in month_ends_inclusive(t0, t1) if t < t1]
            left_year = t0.year - 1

        interpolated = interpolate_wide_interval(v0, v1, t0, t1, month_list)
        for t, series in interpolated.items():
            part = series.rename("forecast_value").reset_index()
            if part.columns[0] != "LSOA21CD":
                part = part.rename(columns={part.columns[0]: "LSOA21CD"})
            part["period_end"] = t
            part["period"] = t.strftime("%Y-%m")
            part["is_anchor"] = t == t0
            part["is_interpolated"] = t != t0
            part["source_year"] = left_year
            part["anchor_date"] = t0
            rows.append(part)

    # Final anchor
    t_last = dates[-1]
    last = wide[t_last].rename("forecast_value").reset_index()
    if last.columns[0] != "LSOA21CD":
        last = last.rename(columns={last.columns[0]: "LSOA21CD"})
    last["period_end"] = t_last
    last["period"] = t_last.strftime("%Y-%m")
    last["is_anchor"] = True
    last["is_interpolated"] = False
    last["source_year"] = t_last.year - 1
    last["anchor_date"] = t_last
    rows.append(last)

    out = pd.concat(rows, ignore_index=True)
    out["time_grain"] = TIME_GRAIN_MONTH
    out["tech_type"] = "EV"
    out["EV_Type"] = ev_type
    out["forecast_source"] = forecast_source
    out["forecast_scenario"] = forecast_scenario
    out["dfes_pathway"] = dfes_pathway
    out["building_block_id"] = building_block_id
    out["observation_source"] = forecast_source
    out["metric"] = "stock"
    return out
