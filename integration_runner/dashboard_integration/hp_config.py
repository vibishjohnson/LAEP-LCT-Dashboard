"""HP dashboard integration paths and locked conventions.

Validated HP production is read-only. This module does not import or alter
HP canonicalisation, topology, or annual forecast methodology.
"""

from pathlib import Path

from .config import (
    DASHBOARD,
    DASHBOARD_INTEGRATION_PUBLISH,
    DASHBOARD_ONLY_COUNT,
    FLOAT_TOL,
    GEO_TOL,
    HT_ONLY_COUNT,
    INTEGRATION_DIR,
    NESO_LSOA_COUNT,
    OUTPUT_DIR,
    QA_DIR,
    UKPN_DNOS,
    local_path,
)

HP_PROJECT = local_path("hp_project", "LCT_HP_PROJECT")
HP_FINAL = HP_PROJECT / "hp_ht_ukpn_final_outputs"
HP_OUTPUT_DIR = OUTPUT_DIR
HP_QA_DIR = QA_DIR

NESO_HP_FILES = {
    ("HolisticTransition", "Lct_BB005", "Domestic heat pump"): HP_FINAL
    / "HolisticTransition"
    / "Lct_BB005_lsoa_forecast.csv",
    ("HolisticTransition", "Lct_BB006", "Domestic hybrid heat pump"): HP_FINAL
    / "HolisticTransition"
    / "Lct_BB006_lsoa_forecast.csv",
    ("HolisticTransitionBasic", "Lct_BB005", "Domestic heat pump"): HP_FINAL
    / "HolisticTransitionBasic"
    / "Lct_BB005_lsoa_forecast.csv",
    ("HolisticTransitionBasic", "Lct_BB006", "Domestic hybrid heat pump"): HP_FINAL
    / "HolisticTransitionBasic"
    / "Lct_BB006_lsoa_forecast.csv",
}

HP_LSOA_LOOKUP = HP_FINAL / "lsoa_to_ukpn_gsp_lookup.csv"
HP_ACCOUNTING = HP_FINAL / "run_summary.csv"
HP_EXCLUSION_A = HP_FINAL / "dfes_exclusion_a.csv"

BB_TO_HP_DETAIL = {
    "Lct_BB005": "Domestic heat pump",
    "Lct_BB006": "Domestic hybrid heat pump",
}
HP_DETAIL_TO_BB = {v: k for k, v in BB_TO_HP_DETAIL.items()}

TECH_TYPE_HP = "Heat Pump"
DFES_SCENARIO_WORLD = "HolisticTransition"
DFES_SHEET = "Domestic Heating"

DFES_HP_WORKBOOKS = (
    DASHBOARD / "dfes" / "DFES 2026" / "UKPN-domestic-heating-scenarios-LSOA-w-LAEP.xlsx",
    HP_PROJECT / "UKPN-domestic-heating-scenarios-LSOA-w-LAEP.xlsx",
)

DASHBOARD_COMPARISON_DNO = DASHBOARD / "project" / "output_processed" / "dashboard_comparison_dno_monthly.csv"
DASHBOARD_COMPARISON_LA = DASHBOARD / "project" / "output_processed" / "dashboard_comparison_la.csv"
DASHBOARD_COMPARISON_LAEP = DASHBOARD / "project" / "output_processed" / "dashboard_comparison_laep.csv"
OLD_DFES_HP_MONTHLY = DASHBOARD / "project" / "output_processed" / "dfes_heat_pump_forecast_holistic_transition.csv"

EXCLUDED_DFES_LSOAS = frozenset(
    [
        "E01001196",
        "E01001239",
        "E01001244",
        "E01001281",
        "E01001377",
        "E01002579",
        "E01002693",
        "E01003871",
        "E01030920",
        "E01032572",
        "E01032610",
        "E01034452",
    ]
)

# Existing HP chart window in dashboard_comparison_*.csv. Underlying data runs to 2051-03.
HP_DISPLAY_START_PERIOD = "2025-04"
HP_DISPLAY_END_PERIOD = "2026-03"
HP_ACTUAL_WINDOW_START = "2025-04"
HP_ACTUAL_WINDOW_END = "2026-03"

SPECIAL_SPATIAL_GROUPS = {
    "Pelham / Wymondley / Twinstead": ("Pelham and Wymondley",),
    "Ninfield / Little Horsted": ("Ninfield SGT",),
    "Braintree / Rayleigh": ("Braintree and Rayleigh",),
    "Kemsley": ("Kemsley SGT",),
    "Sellindge / Richborough": ("Sellindge SGT2 & 3 & Richborough SGT",),
    "Willesden EPN": ("Willesden 132kV (EPN)",),
    "Willesden LPN": ("Willesden 132kV (LPN)",),
    "Barking LPN": ("Barking C 132kV (LPN)",),
    "Wimbledon South 3/4": ("Wimbledon Grid South 3/4 132kV",),
}

NEW_CROSS_SPATIAL = (
    "New Cross 132kV",
    "New Cross 66kV",
    "Wimbledon Grid South 1/2 132kV",
    "Wimbledon Grid South 3/4 132kV",
)

HP_PUBLISH_FILES = (
    "hp_monthly_forecast_dno.csv",
    "hp_monthly_forecast_la.csv",
    "hp_monthly_forecast_laep.csv",
    "hp_actuals_dno.csv",
    "hp_actuals_la.csv",
    "hp_actuals_laep.csv",
    "hp_metadata.json",
    "hp_run_report.md",
)

__all__ = [
    "DASHBOARD",
    "DASHBOARD_INTEGRATION_PUBLISH",
    "DASHBOARD_ONLY_COUNT",
    "FLOAT_TOL",
    "GEO_TOL",
    "HT_ONLY_COUNT",
    "INTEGRATION_DIR",
    "NESO_LSOA_COUNT",
    "OUTPUT_DIR",
    "QA_DIR",
    "UKPN_DNOS",
    "HP_FINAL",
    "HP_OUTPUT_DIR",
    "HP_QA_DIR",
    "NESO_HP_FILES",
    "HP_LSOA_LOOKUP",
    "HP_ACCOUNTING",
    "EXCLUDED_DFES_LSOAS",
    "BB_TO_HP_DETAIL",
    "TECH_TYPE_HP",
]
