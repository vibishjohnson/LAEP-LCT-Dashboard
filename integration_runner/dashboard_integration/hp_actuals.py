"""HP actuals: copy existing dashboard comparison observations without alteration."""

from __future__ import annotations

import pandas as pd

from .hp_config import (
    DASHBOARD_COMPARISON_DNO,
    DASHBOARD_COMPARISON_LA,
    DASHBOARD_COMPARISON_LAEP,
    HP_ACTUAL_WINDOW_END,
    HP_ACTUAL_WINDOW_START,
    TECH_TYPE_HP,
    UKPN_DNOS,
)


def _month_end(period: str) -> str:
    year, month = period.split("-")
    last = {
        "01": "31",
        "02": "28",
        "03": "31",
        "04": "30",
        "05": "31",
        "06": "30",
        "07": "31",
        "08": "31",
        "09": "30",
        "10": "31",
        "11": "30",
        "12": "31",
    }[month]
    if month == "02" and int(year) % 4 == 0 and (int(year) % 100 != 0 or int(year) % 400 == 0):
        last = "29"
    return f"{year}-{month}-{last}"


def _stamp(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["period_end"] = out["period"].map(_month_end)
    out["time_grain"] = "month"
    out["tech_type"] = TECH_TYPE_HP
    out["observation_source"] = "ACTUAL"
    out["forecast_source"] = pd.NA
    out["forecast_scenario"] = pd.NA
    out["dfes_pathway"] = pd.NA
    out["building_block_id"] = pd.NA
    out["metric"] = "monthly_installations"
    out["is_interpolated"] = False
    out["is_anchor"] = False
    out["source_year"] = pd.NA
    out["anchor_date"] = pd.NA
    out["value"] = pd.to_numeric(out["actual_install_count"], errors="coerce").fillna(0.0)
    return out


def _window(df: pd.DataFrame) -> pd.DataFrame:
    out = df[df["tech_type"] == TECH_TYPE_HP].copy()
    out["period"] = out["period"].astype(str).str[:7]
    out = out[(out["period"] >= HP_ACTUAL_WINDOW_START) & (out["period"] <= HP_ACTUAL_WINDOW_END)]
    return out


def load_hp_actuals() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, str, dict]:
    """Return DNO/LA/LAEP actuals copied from existing dashboard comparison files.

    The FY 2025/26 window is the Stage 2E/2F closed methodology window, not
    max(date) of the raw registers. Values are not recomputed.
    """
    dno_src = pd.read_csv(DASHBOARD_COMPARISON_DNO)
    la_src = pd.read_csv(DASHBOARD_COMPARISON_LA)
    laep_src = pd.read_csv(DASHBOARD_COMPARISON_LAEP)

    dno = _window(dno_src)
    dno = dno[dno["DNO"].isin(UKPN_DNOS)].copy()
    la = _window(la_src)
    laep = _window(laep_src)

    if dno.empty:
        raise ValueError("No Heat Pump actuals in dashboard_comparison_dno_monthly.csv")

    keep_dno = [
        "period",
        "DNO",
        "actual_install_count",
        "actual_total_kw",
        "actual_cumulative_count",
        "actual_cumulative_kw",
        "dfes_opening_stock",
        "dfes_closing_stock",
    ]
    keep_la = [
        "period",
        "LAD22CD",
        "LAD22NM",
        "actual_install_count",
        "actual_total_kw",
        "actual_cumulative_count",
        "actual_cumulative_kw",
        "dfes_opening_stock",
        "dfes_closing_stock",
    ]
    keep_laep = [
        "period",
        "LAEP",
        "actual_install_count",
        "actual_total_kw",
        "actual_cumulative_count",
        "actual_cumulative_kw",
        "dfes_opening_stock",
        "dfes_closing_stock",
    ]
    dno_out = _stamp(dno[keep_dno])
    la_out = _stamp(la[keep_la])
    laep_out = _stamp(laep[keep_laep])

    latest = str(sorted(dno_out["period"].unique())[-1])
    notes = {
        "source": "dashboard_comparison_* (Stage 2F; Stage 2D INCLUDE observations)",
        "grain": "month",
        "window": f"{HP_ACTUAL_WINDOW_START} to {HP_ACTUAL_WINDOW_END}",
        "latest_complete_period": latest,
        "window_rule": (
            "Closed FY 2025/26 window from Stage 2E/2F, not max(date) of raw registers. "
            "All twelve months in the window are retained, including zero-install months."
        ),
        "n_dno": int(len(dno_out)),
        "n_la": int(len(la_out)),
        "n_laep": int(len(laep_out)),
        "dno_install_sum": float(dno_out["actual_install_count"].sum()),
    }
    return dno_out, la_out, laep_out, latest, notes
