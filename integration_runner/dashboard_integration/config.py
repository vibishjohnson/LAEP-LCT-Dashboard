"""Paths and locked conventions for the parallel dashboard integration."""

import json
import os
from pathlib import Path

INTEGRATION_DIR = Path(__file__).resolve().parent
EV_PROJECT = INTEGRATION_DIR.parent


def local_path(key: str, env: str) -> Path:
    """Machine-specific location: environment variable `env`, else `key` in local_paths.json beside this package.

    local_paths.json is not version-controlled (copy local_paths.example.json). If neither is set, the result is an
    inert placeholder inside this package folder, so imports still work but any input read fails, naming the setting.
    """
    value = os.environ.get(env)
    if not value:
        try:
            value = json.loads((EV_PROJECT / "local_paths.json").read_text(encoding="utf-8")).get(key)
        except (OSError, ValueError):
            value = None
    return Path(value) if value else INTEGRATION_DIR / f"__{env}_not_configured__"


DASHBOARD = local_path("dashboard_root", "LCT_DASHBOARD_ROOT")

HT_FINAL = EV_PROJECT / "ht_ukpn_final_outputs"
OUTPUT_DIR = INTEGRATION_DIR / "outputs"
QA_DIR = INTEGRATION_DIR / "integration_qa"

NESO_FILES = {
    ("HolisticTransition", "Lct_BB001", "BEV"): HT_FINAL / "HolisticTransition" / "Lct_BB001_lsoa_forecast.csv",
    ("HolisticTransition", "Lct_BB002", "PHEV"): HT_FINAL / "HolisticTransition" / "Lct_BB002_lsoa_forecast.csv",
    ("HolisticTransitionBasic", "Lct_BB001", "BEV"): HT_FINAL / "HolisticTransitionBasic" / "Lct_BB001_lsoa_forecast.csv",
    ("HolisticTransitionBasic", "Lct_BB002", "PHEV"): HT_FINAL / "HolisticTransitionBasic" / "Lct_BB002_lsoa_forecast.csv",
}

HT_LSOA_LOOKUP = HT_FINAL / "lsoa_to_ukpn_gsp_lookup.csv"

DFES_DIR = DASHBOARD / "dfes" / "DFES 2026"
DFES_CARS_VANS = DFES_DIR / "UKPN-electric-car-van-scenarios-LSOA UPDATED.xlsx"
DFES_TAXI_PHV = DFES_DIR / "UKPN-electric-taxi-phv-scenarios-LSOA UPDATED.xlsx"
DFES_MOTO = DFES_DIR / "UKPN-electric-motorcycle-scenarios-LSOA-w-LAEP UPDATED.xlsx"

LSOA_TO_DNO = DASHBOARD / "lookups" / "LSOA to DNO.csv"
LA_LAEP = DASHBOARD / "lookups" / "LA-LAEP lookup.csv"
LSOA_LAD_ONS = (
    DASHBOARD
    / "lookups"
    / "LSOA_(2011)_to_LSOA_(2021)_to_Local_Authority_District_(2022)_Exact_Fit_Lookup_for_EW_(V3).csv"
)
EV_QUARTERLY = DASHBOARD / "project" / "output_processed" / "ev_quarterly_lsoa.csv"
OLD_DFES_MONTHLY = DASHBOARD / "project" / "output_processed" / "dfes_ev_forecast_all.csv"
DASHBOARD_INTEGRATION_PUBLISH = DASHBOARD / "project" / "output_processed" / "integration"

DFES_SCENARIO_WORLD = "HolisticTransition"
TECH_TYPE = "EV"
METRIC_STOCK = "stock"
TIME_GRAIN_MONTH = "month"
TIME_GRAIN_ANNUAL = "annual"
TIME_GRAIN_QUARTER = "quarter"

BB_TO_EV_TYPE = {"Lct_BB001": "BEV", "Lct_BB002": "PHEV"}

DFES_BEV_PARAMS = {
    "BEV Cars": "Cars (BEV)",
    "BEV Vans": "Vans (BEV)",
    "EV Taxis": "Taxis (BEV)",
    "EV PHVs": "PHVs (BEV)",
    "EV Motorcycles": "Motorcycles (BEV)",
}
DFES_PHEV_PARAMS = {
    "PHEV Cars": "Cars (PHEV)",
    "PHEV Vans": "Vans (PHEV)",
}

# User-published rounded all-years totals. Exact floats are also checked against HT files.
PUBLISHED_NESO_TOTALS = {
    ("HolisticTransition", "Lct_BB001"): 179_617_757,
    ("HolisticTransition", "Lct_BB002"): 12_954_004,
    ("HolisticTransitionBasic", "Lct_BB001"): 176_822_397,
    ("HolisticTransitionBasic", "Lct_BB002"): 13_820_698,
}

FLOAT_TOL = 1e-6
GEO_TOL = 1e-4
NESO_LSOA_COUNT = 11_077
DASHBOARD_LOOKUP_COUNT = 11_023
HT_ONLY_COUNT = 66
DASHBOARD_ONLY_COUNT = 12

UKPN_DNOS = ("EPN", "LPN", "SPN")
EV_ACTUAL_FROM_PERIOD = "2025-03"
