#!/usr/bin/env python3
"""
LCT Actuals Processing - LSOA-level pipeline for LAEP
Ingests 6 sources, standardises to LSOA21CD, deduplicates by MPAN, outputs LSOA-month-tech aggregates.
"""

import os
import re
import glob
import warnings
from datetime import datetime
from typing import Optional, List, Dict, Tuple

import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Paths (relative to project root when running: python scripts/02_lct_actuals_processing.py)
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INPUT_RAW_LCT = os.path.join(PROJECT_ROOT, "project", "input_raw", "lct")
INPUT_RAW_MCS = os.path.join(INPUT_RAW_LCT, "mcs")
# Lookups now live directly under `<project_root>/lookups`
INPUT_LOOKUPS = os.path.join(PROJECT_ROOT, "lookups")
OUTPUT_PROCESSED = os.path.join(PROJECT_ROOT, "project", "output_processed")


# Technology standardisation mapping (output names per spec)
TECH_MAPPING = {
    # Databricks consolidated format (current)
    "solar pv": "Solar_PV",
    "solar photovoltaic": "Solar_PV",
    "solar thermal": "Solar_PV",  # Treating thermal as PV for aggregation
    "solar keymark": "Solar_PV",  # Keymark is solar standard cert
    "ev charging point": "EVCP_Private",  # LCT Register format (will be filtered for public)
    "battery storage": "Battery_Storage",
    "battery": "Battery_Storage",
    "heat pump": "Heat_Pump",
    "air source heat pump": "Heat_Pump",
    "ground/water source heat pump": "Heat_Pump",
    "ground source heat pump": "Heat_Pump",
    "water source heat pump": "Heat_Pump",
    "v2g - ev charging point": "Stored_Energy",
    "v2g": "Stored_Energy",
    # Legacy format (for backwards compatibility)
    "solar": "Solar_PV",
    "photovoltaic": "Solar_PV",
    "storage": "Battery_Storage",
    "heatpump": "Heat_Pump",
    "ev charging": "EVCP_Private",
    "ev charge": "EVCP_Private",
    "electric vehicle": "EVCP_Private",
    "vehicle to grid": "Stored_Energy",
    "biomass": "Biomass",
    "wind": "Wind",
    "stored energy": "Stored_Energy",
}

# MPAN dedup priority (lower = higher priority)
SOURCE_PRIORITY = {
    "MCS": 1,
    "LCT_Register": 2,
    "SmartConnect": 3,
    "ECR_GT_1MW": 4,
    "ECR_LT_1MW": 5,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def standardize_postcode(value) -> str:
    """Uppercase and remove ALL spaces for postcode joins."""
    if pd.isna(value):
        return ""
    return str(value).upper().replace(" ", "").strip()


def standardise_postcode(value) -> str:
    """Backward-compatible alias for standardize_postcode."""
    return standardize_postcode(value)


def standardise_technology(tech_name: str, source: str) -> Optional[str]:
    """Map to canonical tech: Solar_PV, Battery_Storage, Heat_Pump, EVCP_Private, EVCP_Public, Wind, Biomass, Stored_Energy."""
    if pd.isna(tech_name):
        return None
    tech_lower = str(tech_name).lower().strip()
    for key, value in TECH_MAPPING.items():
        if key in tech_lower:
            return value
    return str(tech_name).lower().title().replace(" ", "_")


def parse_capacity_to_kw(capacity_value, source: Optional[str] = None) -> float:
    """Parse capacity values and convert to kW."""
    if pd.isna(capacity_value):
        return 0.0
    capacity_str = str(capacity_value).strip()
    numeric_match = re.search(r"[\d,]+\.?\d*", capacity_str.replace(",", ""))
    if not numeric_match:
        return 0.0
    numeric_value = float(numeric_match.group().replace(",", ""))
    capacity_lower = capacity_str.lower()
    if "mw" in capacity_lower:
        return numeric_value * 1000
    if "w" in capacity_lower and "kw" not in capacity_lower:
        return numeric_value / 1000
    if source in ("ECR_GT_1MW", "ECR_LT_1MW"):
        return numeric_value * 1000
    return numeric_value


def parse_date_to_period(date_value) -> Optional[str]:
    """Parse date to YYYY-MM-01 period string. Returns None if unparseable."""
    if pd.isna(date_value):
        return None
    try:
        if isinstance(date_value, str):
            for fmt in [
                "%Y-%m-%d",
                "%d/%m/%Y",
                "%m/%d/%Y",
                "%Y-%m-%d %H:%M:%S",
                "%d/%m/%Y %H:%M",
                "%d/%m/%Y %H:%M:%S",
                "%d-%m-%Y",
                "%Y/%m/%d",
                "%d.%m.%Y",
                "%d %b %Y",
                "%d %B %Y",
                "%Y-%m-%dT%H:%M:%S",
                "%a, %d %b %Y %H:%M %p",
                "%a, %d %b %Y %I:%M %p",
            ]:
                try:
                    dt = datetime.strptime(str(date_value).strip(), fmt)
                    return dt.strftime("%Y-%m-01")
                except ValueError:
                    continue
        dt = pd.to_datetime(date_value)
        return dt.to_period("M").to_timestamp().strftime("%Y-%m-01")
    except Exception:
        return None


def extract_month_from_filename(filepath: str) -> Optional[str]:
    """Extract YYYY-MM from filename (e.g. 'October 2025' -> 2025-10)."""
    filename = os.path.basename(filepath)
    month_match = re.search(r"(\w+)\s+(\d{4})", filename, re.IGNORECASE)
    if month_match:
        try:
            month_name = month_match.group(1)
            year = month_match.group(2)
            month_num = datetime.strptime(month_name, "%B").month
            return f"{year}-{month_num:02d}-01"
        except ValueError:
            pass
    return None


def classify_g98_g99(
    source: str,
    technology: Optional[str],
    capacity_kw: float,
    no_phases: Optional[float] = None,
) -> Optional[str]:
    """
    Classify connection as G98 or G99 using source- and phase-specific rules.

    Rules:
      - These rules do NOT apply to EVCPs or Heat Pumps (return None for those).
      - All ECR (GT/LT) are G99.
      - LCT_Register:
          per_phase_kw = capacity_kw / no_phases
          if per_phase_kw <= 3.64 → G98, else G99.
      - MCS and SmartConnect:
          if capacity_kw <= 11.04 → G98, else G99.
    """
    if technology in ("Heat_Pump", "EVCP_Private", "EVCP_Public"):
        return None

    if source in ("ECR_GT_1MW", "ECR_LT_1MW"):
        return "G99"

    if source == "LCT_Register":
        try:
            phases = float(no_phases) if no_phases is not None and not pd.isna(no_phases) else 1.0
            if phases <= 0:
                phases = 1.0
        except Exception:
            phases = 1.0
        per_phase_kw = capacity_kw / phases if phases > 0 else capacity_kw
        return "G98" if per_phase_kw <= 3.64 else "G99"

    if source in ("MCS", "SmartConnect"):
        return "G98" if capacity_kw <= 11.04 else "G99"

    return None


def classify_ev_speed(capacity_kw: float) -> Optional[str]:
    """
    Classify EV charge points as Slow / Fast for reporting.

    - Slow: capacity_kw <= 3.68
    - Fast: capacity_kw > 3.68
    """
    try:
        if pd.isna(capacity_kw):
            return None
    except Exception:
        return None
    return "Slow" if capacity_kw <= 3.68 else "Fast"


def load_geo_lookups() -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Load postcode→LSOA11 and LSOA11→(LSOA21, LAD22) lookups.

    Files (in project/input_raw/lookups/):
      - postcode_to_lsoa11.csv  with columns incl. pcds, lsoa11cd
      - lsoa11_to_lsoa21_lad22.csv with columns LSOA11CD, LSOA21CD, LAD22CD
    """
    lookup_dir = INPUT_LOOKUPS
    if not os.path.exists(lookup_dir):
        lookup_dir = os.path.join(PROJECT_ROOT, "project", "input_raw", "lookups")

    # Postcode -> LSOA11 (real file: Postcode-LSOA-LAD lookup.csv)
    pc_path = os.path.join(lookup_dir, "Postcode-LSOA-LAD lookup.csv")
    if not os.path.exists(pc_path):
        print("  Postcode-LSOA-LAD lookup.csv not found - geography resolution will be limited")
        pc_df = pd.DataFrame(columns=["pcds_std", "LSOA11CD"])
    else:
        pc_df_raw = None
        for enc in ("utf-8", "latin-1", "cp1252"):
            try:
                pc_df_raw = pd.read_csv(pc_path, encoding=enc)
                break
            except Exception:
                continue
        if pc_df_raw is None:
            print("  Failed to read Postcode-LSOA-LAD lookup.csv with common encodings")
            pc_df = pd.DataFrame(columns=["pcds_std", "LSOA11CD"])
        else:
            # Detect columns by name (case-insensitive)
            pcd_col = None
            lsoa11_col = None
            for c in pc_df_raw.columns:
                lc = c.lower()
                if lc == "pcds":
                    pcd_col = c
                elif lc == "lsoa11cd":
                    lsoa11_col = c
            if pcd_col is None:
                # Fallback: first column for postcode
                pcd_col = pc_df_raw.columns[0]
            if lsoa11_col is None:
                # Fallback: any column containing 'lsoa11'
                for c in pc_df_raw.columns:
                    if "lsoa11" in c.lower():
                        lsoa11_col = c
                        break
            if lsoa11_col is None:
                print("  Warning: postcode_to_lsoa11.csv missing LSOA11 column; geography resolution disabled")
                pc_df = pd.DataFrame(columns=["pcds_std", "LSOA11CD"])
            else:
                pc_df = pc_df_raw[[pcd_col, lsoa11_col]].drop_duplicates()
                pc_df["pcds_std"] = pc_df[pcd_col].apply(standardize_postcode)
                pc_df["LSOA11CD"] = pc_df[lsoa11_col].astype(str).str.strip()
                pc_df = pc_df[pc_df["pcds_std"].str.len() > 0]
                print(f"Postcode-LSOA11 lookup: {len(pc_df)} rows")

    # LSOA11 -> LSOA21 + LAD22
    bridge_path = os.path.join(
        lookup_dir,
        "LSOA_(2011)_to_LSOA_(2021)_to_Local_Authority_District_(2022)_Exact_Fit_Lookup_for_EW_(V3).csv",
    )
    if not os.path.exists(bridge_path):
        print("  LSOA11→LSOA21→LAD22 bridge file not found - geography resolution will be limited")
        bridge_df = pd.DataFrame(columns=["LSOA11CD", "LSOA21CD", "LAD22CD"])
    else:
        b_raw = None
        for enc in ("utf-8", "latin-1", "cp1252"):
            try:
                b_raw = pd.read_csv(bridge_path, encoding=enc)
                break
            except Exception:
                continue
        if b_raw is None:
            print("  Failed to read LSOA11→LSOA21→LAD22 bridge file with common encodings")
            bridge_df = pd.DataFrame(columns=["LSOA11CD", "LSOA21CD", "LAD22CD"])
        else:
            l11_col = None
            l21_col = None
            lad_col = None
            for c in b_raw.columns:
                lc = c.lower()
                if lc == "lsoa11cd":
                    l11_col = c
                elif lc == "lsoa21cd":
                    l21_col = c
                elif lc == "lad22cd":
                    lad_col = c
            # Allow slight variations
            if l11_col is None:
                for c in b_raw.columns:
                    if "lsoa11" in c.lower():
                        l11_col = c
                        break
            if l21_col is None:
                for c in b_raw.columns:
                    if "lsoa21" in c.lower():
                        l21_col = c
                        break
            if lad_col is None:
                for c in b_raw.columns:
                    if "lad22" in c.lower():
                        lad_col = c
                        break

            if l11_col is None or l21_col is None or lad_col is None:
                print("  Warning: lsoa11_to_lsoa21_lad22.csv missing required columns; geography resolution disabled")
                bridge_df = pd.DataFrame(columns=["LSOA11CD", "LSOA21CD", "LAD22CD"])
            else:
                bridge_df = b_raw[[l11_col, l21_col, lad_col]].drop_duplicates()
                bridge_df = bridge_df.rename(
                    columns={
                        l11_col: "LSOA11CD",
                        l21_col: "LSOA21CD",
                        lad_col: "LAD22CD",
                    }
                )
                for c in ["LSOA11CD", "LSOA21CD", "LAD22CD"]:
                    bridge_df[c] = bridge_df[c].astype(str).str.strip()
                print(f"LSOA11-(LSOA21,LAD22) bridge: {len(bridge_df)} rows")

    return pc_df, bridge_df


def resolve_geo(postcode, pc_lookup: pd.DataFrame, bridge_lookup: pd.DataFrame) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Resolve postcode → (LSOA21CD, LAD22CD, LSOA11CD).

    Returns (None, None, None) if not resolvable.
    """
    if pd.isna(postcode):
        return None, None, None
    std_pc = standardize_postcode(postcode)
    if not std_pc:
        return None, None, None
    if pc_lookup is None or bridge_lookup is None or pc_lookup.empty or bridge_lookup.empty:
        return None, None, None

    pc_match = pc_lookup[pc_lookup["pcds_std"] == std_pc]
    if pc_match.empty:
        return None, None, None

    lsoa11 = str(pc_match.iloc[0]["LSOA11CD"]).strip()
    if not lsoa11:
        return None, None, None

    br = bridge_lookup[bridge_lookup["LSOA11CD"] == lsoa11]
    if br.empty:
        return None, None, lsoa11

    lsoa21 = str(br.iloc[0]["LSOA21CD"]).strip()
    lad22 = str(br.iloc[0]["LAD22CD"]).strip()
    lsoa21 = lsoa21 or None
    lad22 = lad22 or None
    return lsoa21, lad22, lsoa11


# ---------------------------------------------------------------------------
# Source ingestion
# ---------------------------------------------------------------------------


def _get_mcs_dir():
    """Resolve MCS directory: project/input_raw/lct/mcs or lct/MCS."""
    for base in [
        os.path.join(PROJECT_ROOT, "project", "input_raw", "lct"),
        os.path.join(PROJECT_ROOT, "lct"),
    ]:
        for sub in ("mcs", "MCS"):
            p = os.path.join(base, sub)
            if os.path.exists(p):
                return p
    return os.path.join(PROJECT_ROOT, "project", "input_raw", "lct", "mcs")


def process_mcs(pc_lookup: pd.DataFrame, bridge_lookup: pd.DataFrame) -> Tuple[List[dict], Dict]:
    """Process MCS monthly files. Returns (records, stats)."""
    records = []
    stats = {"ingested": 0, "dropped_no_date": 0, "dropped_no_tech": 0}

    mcs_dir = _get_mcs_dir()
    if not os.path.exists(mcs_dir):
        print("  MCS directory not found")
        return records, stats

    patterns = [
        os.path.join(mcs_dir, "*.xlsx"),
        os.path.join(mcs_dir, "*.xls"),
        os.path.join(mcs_dir, "*.csv"),
    ]
    files = []
    for p in patterns:
        files.extend(glob.glob(p))

    for filepath in files:
        try:
            if filepath.lower().endswith(".csv"):
                df = pd.read_csv(filepath)
            else:
                df = pd.read_excel(filepath)
        except Exception as e:
            print(f"  Error reading {os.path.basename(filepath)}: {e}")
            continue

        if df.empty:
            continue

        # Vectorised postcode→LSOA join for performance
        df["postcode_raw"] = df.get("Postcode")
        df["pcds_std"] = df["postcode_raw"].apply(standardize_postcode)
        if not pc_lookup.empty:
            df = df.merge(
                pc_lookup[["pcds_std", "LSOA11CD"]],
                on="pcds_std",
                how="left",
            )
        else:
            df["LSOA11CD"] = None

        if not bridge_lookup.empty:
            df = df.merge(
                bridge_lookup[["LSOA11CD", "LSOA21CD", "LAD22CD"]],
                on="LSOA11CD",
                how="left",
            )
        else:
            df["LSOA21CD"] = None
            df["LAD22CD"] = None

        fallback_period = extract_month_from_filename(filepath)

        for _, row in df.iterrows():
            tech_raw = row.get("Technology Type")
            tech = standardise_technology(tech_raw, "MCS")
            if not tech:
                stats["dropped_no_tech"] += 1
                continue

            capacity_kw = parse_capacity_to_kw(row.get("Total Installed Capacity"))
            if tech == "Battery_Storage" and pd.notna(row.get("Battery Max AC Power Output")):
                battery_kw = parse_capacity_to_kw(row.get("Battery Max AC Power Output"))
                if battery_kw > 0:
                    capacity_kw = battery_kw

            period = parse_date_to_period(row.get("Commissioning Date"))
            if not period:
                period = fallback_period
            if not period:
                stats["dropped_no_date"] += 1
                continue

            mpan = row.get("MPAN")
            if pd.notna(mpan):
                mpan = str(mpan).strip()
            else:
                mpan = None

            g99_status = classify_g98_g99("MCS", tech, capacity_kw)

            records.append(
                {
                    "period": period,
                    "technology": tech,
                    "LSOA21CD": row.get("LSOA21CD"),
                    "LAD22CD": row.get("LAD22CD"),
                    "LSOA11CD": row.get("LSOA11CD"),
                    "capacity_kw": capacity_kw,
                    "g99_status": g99_status,
                    "source": "MCS",
                    "mpan": mpan,
                    "postcode_raw": row.get("postcode_raw"),
                }
            )
            stats["ingested"] += 1

    return records, stats


def process_ecr_gt_1mw(pc_lookup: pd.DataFrame, bridge_lookup: pd.DataFrame) -> Tuple[List[dict], Dict]:
    """Process ECR >1MW. Returns (records, stats)."""
    records = []
    stats = {"ingested": 0, "dropped_no_date": 0, "dropped_status": 0, "dropped_no_tech": 0}

    filepath = os.path.join(INPUT_RAW_LCT, "ecr_gt_1mw.xlsx")
    if not os.path.exists(filepath):
        filepath = os.path.join(PROJECT_ROOT, "lct", "ecr_large.csv")
    if not os.path.exists(filepath):
        print("  ECR_GT_1MW file not found")
        return records, stats

    try:
        if filepath.lower().endswith(".csv"):
            df = pd.read_csv(filepath)
        else:
            df = pd.read_excel(filepath)
    except Exception as e:
        print(f"  Error reading ECR_GT_1MW: {e}")
        return records, stats

    for _, row in df.iterrows():
        status = str(row.get("Connection Status", "")).strip().lower()
        if status != "connected":
            stats["dropped_status"] += 1
            continue

        tech_raw = row.get("Energy Conversion Technology 1") or row.get("Primary Resource Type_Group")
        tech = standardise_technology(tech_raw, "ECR_GT_1MW")
        if not tech:
            stats["dropped_no_tech"] += 1
            continue

        capacity_kw = parse_capacity_to_kw(
            row.get("Already connected Registered Capacity (MW)"), "ECR_GT_1MW"
        )

        postcode = row.get("Postcode")
        lsoa21, lad22, lsoa11 = resolve_geo(postcode, pc_lookup, bridge_lookup)

        period = parse_date_to_period(row.get("Date Connected"))
        if not period:
            stats["dropped_no_date"] += 1
            continue

        mpan = row.get("Export MPAN / MSID")
        if pd.notna(mpan):
            mpan = str(mpan).strip()
        else:
            mpan = None

        g99_status = classify_g98_g99("ECR_GT_1MW", tech, capacity_kw)

        records.append(
            {
                "period": period,
                "technology": tech,
                "LSOA21CD": lsoa21,
                "LAD22CD": lad22,
                "LSOA11CD": lsoa11,
                "capacity_kw": capacity_kw,
                "g99_status": g99_status,
                "source": "ECR_GT_1MW",
                "mpan": mpan,
                "postcode_raw": postcode,
            }
        )
        stats["ingested"] += 1

    return records, stats


def process_ecr_lt_1mw(pc_lookup: pd.DataFrame, bridge_lookup: pd.DataFrame) -> Tuple[List[dict], Dict]:
    """Process ECR <1MW. Returns (records, stats)."""
    records = []
    stats = {"ingested": 0, "dropped_no_date": 0, "dropped_status": 0, "dropped_no_tech": 0}

    filepath = os.path.join(INPUT_RAW_LCT, "ecr_lt_1mw.xlsx")
    if not os.path.exists(filepath):
        filepath = os.path.join(PROJECT_ROOT, "lct", "ecr_small.csv")
    if not os.path.exists(filepath):
        print("  ECR_LT_1MW file not found")
        return records, stats

    try:
        if filepath.lower().endswith(".csv"):
            df = pd.read_csv(filepath)
        else:
            df = pd.read_excel(filepath)
    except Exception as e:
        print(f"  Error reading ECR_LT_1MW: {e}")
        return records, stats

    for _, row in df.iterrows():
        status = str(row.get("Connection Status", "")).strip().lower()
        if status != "connected":
            stats["dropped_status"] += 1
            continue

        tech_raw = row.get("Energy Conversion Technology 1") or row.get("Primary Resource Type_Group")
        tech = standardise_technology(tech_raw, "ECR_LT_1MW")
        if not tech:
            stats["dropped_no_tech"] += 1
            continue

        capacity_kw = parse_capacity_to_kw(
            row.get("Already connected Registered Capacity (MW)"), "ECR_LT_1MW"
        )

        postcode = row.get("Postcode")
        lsoa21, lad22, lsoa11 = resolve_geo(postcode, pc_lookup, bridge_lookup)

        period = parse_date_to_period(row.get("Date Connected"))
        if not period:
            stats["dropped_no_date"] += 1
            continue

        mpan = row.get("Export MPAN / MSID")
        if pd.notna(mpan):
            mpan = str(mpan).strip()
        else:
            mpan = None

        g99_status = classify_g98_g99("ECR_LT_1MW", tech, capacity_kw)

        records.append(
            {
                "period": period,
                "technology": tech,
                "LSOA21CD": lsoa21,
                "LAD22CD": lad22,
                "LSOA11CD": lsoa11,
                "capacity_kw": capacity_kw,
                "g99_status": g99_status,
                "source": "ECR_LT_1MW",
                "mpan": mpan,
                "postcode_raw": postcode,
            }
        )
        stats["ingested"] += 1

    return records, stats


def _is_lct_ev_public_commercial(row) -> bool:
    """Placeholder: exclude LCT_Register rows that indicate public/commercial charge points.
    Check Property_Type, Category, or similar. Update as needed."""
    prop = str(row.get("Property_Type", "")).lower()
    cat = str(row.get("Category", "")).lower()
    if "non domestic" in prop or "commercial" in prop or "public" in prop:
        return True
    if "demand" in cat and "domestic" not in prop:
        return False  # Keep domestic
    return False


def process_lct_register(pc_lookup: pd.DataFrame, bridge_lookup: pd.DataFrame) -> Tuple[List[dict], Dict]:
    """Process LCT Register. EV: keep <=7kW private only. Returns (records, stats)."""
    records = []
    stats = {
        "ingested": 0,
        "dropped_no_date": 0,
        "dropped_status": 0,
        "dropped_no_tech": 0,
        "dropped_ev_public": 0,
        "dropped_ev_gt7": 0,
    }

    filepath = os.path.join(INPUT_RAW_LCT, "lct_register.xlsx")
    if not os.path.exists(filepath):
        filepath = os.path.join(PROJECT_ROOT, "lct", "LCT Register.csv")
    if not os.path.exists(filepath):
        print("  LCT_Register file not found")
        return records, stats

    try:
        if filepath.lower().endswith(".csv"):
            df = pd.read_csv(filepath)
        else:
            df = pd.read_excel(filepath)
    except Exception as e:
        print(f"  Error reading LCT_Register: {e}")
        return records, stats

    if df.empty:
        return records, stats

    # Vectorised postcode→LSOA join
    df["postcode_raw"] = df.get("MPAN_Postcode")
    df["pcds_std"] = df["postcode_raw"].apply(standardize_postcode)
    if not pc_lookup.empty:
        df = df.merge(
            pc_lookup[["pcds_std", "LSOA11CD"]],
            on="pcds_std",
            how="left",
        )
    else:
        df["LSOA11CD"] = None

    if not bridge_lookup.empty:
        df = df.merge(
            bridge_lookup[["LSOA11CD", "LSOA21CD", "LAD22CD"]],
            on="LSOA11CD",
            how="left",
        )
    else:
        df["LSOA21CD"] = None
        df["LAD22CD"] = None

    for _, row in df.iterrows():
        status = str(row.get("Status", "")).strip().lower()
        if status != "connected":
            stats["dropped_status"] += 1
            continue

        tech_raw = row.get("Type")
        tech = standardise_technology(tech_raw, "LCT_Register")
        if not tech:
            stats["dropped_no_tech"] += 1
            continue

        capacity_kw = parse_capacity_to_kw(row.get("Generation_Rating"))
        if capacity_kw == 0 and pd.notna(row.get("Import_Rating")):
            capacity_kw = parse_capacity_to_kw(row.get("Import_Rating"))

        # EV charging: keep only private (public/commercial filtered), no capacity cap
        if tech in ("EVCP_Private", "EV Charging", "Evcp_Private"):
            if _is_lct_ev_public_commercial(row):
                stats["dropped_ev_public"] += 1
                continue
            tech = "EVCP_Private"

        period = parse_date_to_period(row.get("Installation_Date"))
        if not period:
            period = parse_date_to_period(row.get("Commissioning_Date"))
        if not period:
            stats["dropped_no_date"] += 1
            continue

        mpan = row.get("MPAN")
        if pd.notna(mpan):
            mpan = str(mpan).strip()
        else:
            mpan = None

        g99_status = classify_g98_g99("LCT_Register", tech, capacity_kw, row.get("No_Phases"))
        ev_speed = classify_ev_speed(capacity_kw) if tech == "EVCP_Private" else None

        records.append(
            {
                "period": period,
                "technology": tech,
                "LSOA21CD": row.get("LSOA21CD"),
                "LAD22CD": row.get("LAD22CD"),
                "LSOA11CD": row.get("LSOA11CD"),
                "capacity_kw": capacity_kw,
                "g99_status": g99_status,
                "ev_speed": ev_speed,
                "source": "LCT_Register",
                "mpan": mpan,
                "postcode_raw": row.get("postcode_raw"),
            }
        )
        stats["ingested"] += 1

    return records, stats


def process_zapmap(pc_lookup: pd.DataFrame, bridge_lookup: pd.DataFrame) -> Tuple[List[dict], Dict]:
    """Process ZapMap. All records are EVCP_Public. Returns (records, stats)."""
    records = []
    stats = {"ingested": 0, "dropped_no_date": 0, "dropped_no_capacity": 0}

    # Primary location (project-style): project/input_raw/lct/zapmap.csv
    filepath = os.path.join(INPUT_RAW_LCT, "zapmap.csv")
    # Fallback to your provided location: <project_root>/lct/zapmap.csv
    if not os.path.exists(filepath):
        filepath = os.path.join(PROJECT_ROOT, "lct", "zapmap.csv")
    if not os.path.exists(filepath):
        print("  ZapMap file not found")
        return records, stats

    # Read with flexible encodings
    for encoding in ("utf-8", "latin-1", "cp1252"):
        try:
            df = pd.read_csv(filepath, encoding=encoding)
            break
        except Exception:
            continue
    else:
        print("  Could not read ZapMap with any encoding")
        return records, stats

    if df.empty:
        return records, stats

    # -----------------------------
    # Vectorised geography join
    # -----------------------------
    df["postcode_raw"] = df.get("postal_code")
    df["pcds_std"] = df["postcode_raw"].apply(standardize_postcode)

    if not pc_lookup.empty:
        df = df.merge(
            pc_lookup[["pcds_std", "LSOA11CD"]],
            on="pcds_std",
            how="left",
        )
    else:
        df["LSOA11CD"] = None

    if not bridge_lookup.empty:
        df = df.merge(
            bridge_lookup[["LSOA11CD", "LSOA21CD", "LAD22CD"]],
            on="LSOA11CD",
            how="left",
        )
    else:
        df["LSOA21CD"] = None
        df["LAD22CD"] = None

    # -----------------------------
    # Vectorised capacity from power_band_name / power_group / max_power
    # -----------------------------
    if "power_band_name" in df.columns:
        band = df["power_band_name"].astype(str).str.lower()
    else:
        band = pd.Series("", index=df.index)

    if "power_group" in df.columns:
        power_group = df["power_group"].astype(str).str.lower()
    else:
        power_group = pd.Series("", index=df.index)

    if "max_power" in df.columns:
        max_power = df["max_power"]
    else:
        max_power = pd.Series(0, index=df.index)

    capacity_kw = pd.Series(index=df.index, dtype="float64")

    # From power_band_name
    capacity_kw = capacity_kw.mask(band.str.contains("ultra", na=False), 150.0)
    capacity_kw = capacity_kw.mask(
        band.str.contains("rapid", na=False) & ~band.str.contains("ultra", na=False), 43.0
    )
    capacity_kw = capacity_kw.mask(band.str.contains("fast", na=False), 7.64)
    capacity_kw = capacity_kw.mask(band.str.contains("slow", na=False), 3.7)

    # Fallbacks from power_group
    capacity_kw = capacity_kw.mask(
        capacity_kw.isna() & power_group.str.contains("slow", na=False),
        3.7,
    )
    capacity_kw = capacity_kw.mask(
        capacity_kw.isna() & power_group.str.contains("fast", na=False),
        7.64,
    )
    capacity_kw = capacity_kw.mask(
        capacity_kw.isna() & power_group.str.contains("rapid", na=False),
        43.0,
    )

    # Fallback from max_power (watts)
    capacity_kw = capacity_kw.mask(
        capacity_kw.isna() & max_power.notna() & (max_power > 0),
        max_power / 1000.0,
    )

    df["capacity_kw"] = capacity_kw

    # Drop rows where we still have no capacity
    before = len(df)
    df = df[df["capacity_kw"].notna()].copy()
    stats["dropped_no_capacity"] += before - len(df)

    if df.empty:
        return records, stats

    # -----------------------------
    # Build canonical records (loop only for date parsing & final dicts)
    # -----------------------------
    # Use ZapMap date column (includes time, which parse_date_to_period will ignore)
    date_cols = ["zapmap_connector_added_date", "date_connector_added"]

    for _, row in df.iterrows():
        date_val = None
        for c in date_cols:
            if c in df.columns:
                date_val = row.get(c)
                if pd.notna(date_val):
                    break

        period = parse_date_to_period(date_val)
        if not period:
            stats["dropped_no_date"] += 1
            continue

        capacity_val = float(row["capacity_kw"])
        ev_speed = classify_ev_speed(capacity_val)

        records.append(
            {
                "period": period,
                "technology": "EVCP_Public",
                "LSOA21CD": row.get("LSOA21CD"),
                "LAD22CD": row.get("LAD22CD"),
                "LSOA11CD": row.get("LSOA11CD"),
                "capacity_kw": capacity_val,
                "g99_status": None,  # G98/G99 rules do not apply to public EV charge points
                "ev_speed": ev_speed,
                "source": "ZapMap",
                "mpan": None,
                "postcode_raw": row.get("postcode_raw"),
            }
        )
        stats["ingested"] += 1

    return records, stats


def process_smart_connect(pc_lookup: pd.DataFrame, bridge_lookup: pd.DataFrame) -> Tuple[List[dict], Dict]:
    """Process SmartConnect. EV: keep <=7kW only as EVCP_Private. Returns (records, stats)."""
    records = []
    stats = {"ingested": 0, "dropped_no_date": 0, "dropped_status": 0, "dropped_no_tech": 0, "dropped_ev_gt7": 0}

    filepath = os.path.join(INPUT_RAW_LCT, "smart_connect.csv")
    if not os.path.exists(filepath):
        filepath = os.path.join(PROJECT_ROOT, "lct", "Device-Report (2).csv")
    if not os.path.exists(filepath):
        print("  SmartConnect file not found")
        return records, stats

    for encoding in ("utf-8", "latin-1", "cp1252"):
        try:
            if filepath.lower().endswith(".csv"):
                df = pd.read_csv(filepath, encoding=encoding)
            else:
                df = pd.read_excel(filepath)
            break
        except Exception:
            continue
    else:
        print("  Could not read SmartConnect")
        return records, stats

    if df.empty:
        return records, stats

    # Column name variations - map to actual Device-Report columns
    inst_col = "Technology Type" if "Technology Type" in df.columns else "Installation Type"
    status_col = "Actual Status" if "Actual Status" in df.columns else None
    export_col = "Export Capacity Total kW" if "Export Capacity Total kW" in df.columns else "Application Export kW"
    import_col = "Application Import kW" if "Application Import kW" in df.columns else None
    date_col = "Application Date Created" if "Application Date Created" in df.columns else "Date created"
    approval_col = "Approval Date" if "Approval Date" in df.columns else None
    postcode_col = "Postcode" if "Postcode" in df.columns else None

    # Derive a cleaned postcode column
    df["postcode_raw"] = df.get(postcode_col)
    # For rows with missing explicit postcode, try to extract from address
    if "Premise Address" in df.columns:
        mask_missing = df["postcode_raw"].isna()
        if mask_missing.any():
            addr_series = df.loc[mask_missing, "Premise Address"].astype(str)
            extracted = addr_series.str.extract(r"([A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2})", expand=False, flags=re.I)
            df.loc[mask_missing, "postcode_raw"] = extracted

    # Vectorised postcode→LSOA join
    df["pcds_std"] = df["postcode_raw"].apply(standardize_postcode)
    if not pc_lookup.empty:
        df = df.merge(
            pc_lookup[["pcds_std", "LSOA11CD"]],
            on="pcds_std",
            how="left",
        )
    else:
        df["LSOA11CD"] = None

    if not bridge_lookup.empty:
        df = df.merge(
            bridge_lookup[["LSOA11CD", "LSOA21CD", "LAD22CD"]],
            on="LSOA11CD",
            how="left",
        )
    else:
        df["LSOA21CD"] = None
        df["LAD22CD"] = None

    for _, row in df.iterrows():
        # Filter by status only if column exists (Device-Report may not have it)
        if status_col:
            if pd.notna(row.get(status_col)):
                status_lower = str(row.get(status_col)).strip().lower()
                if status_lower not in ("completed", "connected"):
                    stats["dropped_status"] += 1
                    continue

        inst_type = str(row.get(inst_col, "")).lower()
        if "battery" in inst_type:
            tech = "Battery_Storage"
        elif "pv" in inst_type or "solar" in inst_type:
            tech = "Solar_PV"
        elif "heat pump" in inst_type:
            tech = "Heat_Pump"
        elif "electric vehicle" in inst_type or "ev" in inst_type or "charge" in inst_type:
            tech = "EVCP_Private"
        else:
            stats["dropped_no_tech"] += 1
            continue

        capacity_kw = 0.0
        if pd.notna(row.get(export_col)):
            capacity_kw = parse_capacity_to_kw(row.get(export_col))
        elif import_col and pd.notna(row.get(import_col)):
            capacity_kw = parse_capacity_to_kw(row.get(import_col))

        if tech == "EVCP_Private" and capacity_kw > 7:
            stats["dropped_ev_gt7"] += 1
            continue

        period = parse_date_to_period(row.get(date_col))
        if not period and approval_col:
            period = parse_date_to_period(row.get(approval_col))
        if not period:
            stats["dropped_no_date"] += 1
            continue

        mpan = row.get("MPAN")
        if pd.notna(mpan):
            mpan = str(mpan).strip()
        else:
            mpan = None

        g99_status = classify_g98_g99("SmartConnect", tech, capacity_kw)
        ev_speed = classify_ev_speed(capacity_kw) if tech == "EVCP_Private" else None

        records.append(
            {
                "period": period,
                "technology": tech,
                "LSOA21CD": row.get("LSOA21CD"),
                "LAD22CD": row.get("LAD22CD"),
                "LSOA11CD": row.get("LSOA11CD"),
                "capacity_kw": capacity_kw,
                "g99_status": g99_status,
                "ev_speed": ev_speed,
                "source": "SmartConnect",
                "mpan": mpan,
                "postcode_raw": row.get("postcode_raw"),
            }
        )
        stats["ingested"] += 1

    return records, stats


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def deduplicate_by_mpan(records: List[dict]) -> Tuple[List[dict], Dict]:
    """Deduplicate by MPAN using source priority. ZapMap has no MPAN so kept. Returns (deduped, stats)."""
    with_mpan = [r for r in records if r.get("mpan")]
    no_mpan = [r for r in records if not r.get("mpan")]

    mpan_best = {}
    for r in with_mpan:
        mpan = r["mpan"]
        src = r["source"]
        if mpan not in mpan_best or SOURCE_PRIORITY.get(src, 99) < SOURCE_PRIORITY.get(mpan_best[mpan]["source"], 99):
            mpan_best[mpan] = r

    deduped = list(mpan_best.values()) + no_mpan
    dropped = len(with_mpan) - len(mpan_best.values())
    return deduped, {"before": len(records), "after": len(deduped), "dropped": dropped}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    print("=" * 60)
    print("LCT Actuals Processing - LSOA-level pipeline")
    print("=" * 60)
    print("Data sources: Consolidated LCT Register (MCS+SmartConnect), ECR, ZapMap")

    pc_lookup, bridge_lookup = load_geo_lookups()

    all_records = []
    all_stats = {}

    # Ingest (consolidated LCT Register replaces MCS + SmartConnect)
    for name, fn in [
        ("LCT_Register", process_lct_register),
        ("ECR_GT_1MW", process_ecr_gt_1mw),
        ("ECR_LT_1MW", process_ecr_lt_1mw),
        ("ZapMap", process_zapmap),
    ]:
        print(f"\n--- {name} ---")
        recs, st = fn(pc_lookup, bridge_lookup)
        all_records.extend(recs)
        all_stats[name] = st
        print(f"  Ingested: {st.get('ingested', 0)}")
        for k, v in st.items():
            if k != "ingested" and v:
                print(f"  Dropped ({k}): {v}")

    # Summary
    print("\n--- Ingestion summary ---")
    for src, st in all_stats.items():
        print(f"  {src}: {st.get('ingested', 0)} records")

    # Deduplicate
    print("\n--- Deduplication ---")
    deduped, dedup_stats = deduplicate_by_mpan(all_records)
    print(f"  Before: {dedup_stats['before']}, After: {dedup_stats['after']}, Dropped: {dedup_stats['dropped']}")

    # Missing geography
    def _is_missing_lsoa(r):
        v = r.get("LSOA21CD")
        return v is None or (isinstance(v, float) and np.isnan(v)) or str(v).strip() == ""
    missing_geo = [r for r in deduped if _is_missing_lsoa(r)]
    print(f"  Records missing LSOA: {len(missing_geo)}")

    # Aggregate (only records with LSOA)
    with_lsoa = [r for r in deduped if not _is_missing_lsoa(r)]
    df = pd.DataFrame(with_lsoa)
    if df.empty:
        print("\n  No records with LSOA - creating empty output")
        agg = pd.DataFrame(
            columns=[
                "period",
                "technology",
                "LSOA21CD",
                "LAD22CD",
                "g99_status",
                "ev_speed",
                "install_count",
                "total_kw",
            ]
        )
    else:
        # Ensure optional columns exist
        if "g99_status" not in df.columns:
            df["g99_status"] = None
        if "ev_speed" not in df.columns:
            df["ev_speed"] = None

        agg = (
            df.groupby(["period", "technology", "LSOA21CD", "LAD22CD", "g99_status", "ev_speed"], dropna=False)
            .agg(
                install_count=("capacity_kw", "count"),
                total_kw=("capacity_kw", "sum"),
            )
            .reset_index()
        )

    # Output
    os.makedirs(OUTPUT_PROCESSED, exist_ok=True)
    out_path = os.path.join(OUTPUT_PROCESSED, "lsoa_lct_actuals.csv")
    agg.to_csv(out_path, index=False)
    print(f"\n  Written: {out_path} ({len(agg)} rows)")

    missing_path = os.path.join(OUTPUT_PROCESSED, "lsoa_missing_geo.csv")
    if missing_geo:
        mdf = pd.DataFrame(missing_geo)
        # Ensure optional geography columns exist
        if "LSOA11CD" not in mdf.columns:
            mdf["LSOA11CD"] = None
        if "LAD22CD" not in mdf.columns:
            mdf["LAD22CD"] = None
        mdf = mdf[["source", "technology", "period", "mpan", "postcode_raw", "capacity_kw", "LSOA11CD", "LAD22CD"]]
        mdf.to_csv(missing_path, index=False)
        print(f"  Written: {missing_path} ({len(missing_geo)} rows)")
    else:
        pd.DataFrame(
            columns=["source", "technology", "period", "mpan", "postcode_raw", "capacity_kw", "LSOA11CD", "LAD22CD"]
        ).to_csv(missing_path, index=False)
        print(f"  Written: {missing_path} (0 rows)")

    # Final summary
    print("\n--- Final summary ---")
    print(f"  Aggregated rows: {len(agg)}")
    if not agg.empty:
        print(f"  Date range: {agg['period'].min()} to {agg['period'].max()}")
        print(f"  Technologies: {agg['technology'].unique().tolist()}")
    print("\nDone.")


if __name__ == "__main__":
    main()
