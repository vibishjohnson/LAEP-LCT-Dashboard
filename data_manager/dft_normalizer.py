"""
Phase 4: Raw → Canonical DfT Ingestion Layer

Automatically normalizes raw DfT files without requiring manual preprocessing.
Preserves raw uploads unchanged and creates canonical versions for processing.
"""

import os
import csv
import re
from typing import Dict, List, Tuple, Optional
from pathlib import Path
import pandas as pd
from datetime import datetime


class DfTNormalizer:
    """Detect and normalize raw DfT file formats."""

    def __init__(self):
        self.transformation_log = []

    def normalize_veh0135(self, raw_path: str, output_path: str) -> Tuple[bool, Optional[str]]:
        """
        Normalize VEH0135 (LSOA-level EV stock).

        Expected columns: LSOA21CD, Fuel, Keepership, + quarter columns (2025 Q1, 2025 Q2, etc.)
        Usually no preprocessing needed.

        Returns: (success, error_message)
        """
        try:
            df = pd.read_csv(raw_path)

            # Verify required columns exist
            required_cols = ['LSOA21CD', 'Fuel', 'Keepership']
            if not all(col in df.columns for col in required_cols):
                return False, f"VEH0135: Missing required columns {required_cols}"

            # Verify quarter columns exist
            quarter_cols = [c for c in df.columns if re.match(r'^\d{4}\s+Q[1-4]$', str(c))]
            if not quarter_cols:
                return False, "VEH0135: No quarter columns detected"

            # Save canonical
            df.to_csv(output_path, index=False)

            latest_quarter = self._detect_latest_quarter(quarter_cols)
            self.transformation_log.append({
                'file': 'VEH0135',
                'format_recognised': True,
                'normalisation': 'None required',
                'rows_loaded': len(df),
                'latest_period': latest_quarter,
                'columns_detected': len(df.columns)
            })

            return True, None

        except Exception as e:
            return False, f"VEH0135: {str(e)}"

    def normalize_veh0142(self, raw_path: str, output_path: str) -> Tuple[bool, Optional[str]]:
        """
        Normalize VEH0142 (LAD-level body-type proportions).

        Often has introductory/metadata rows before the actual table.

        Expected columns: BodyType, Fuel, Keepership, ONS Code, + quarter columns

        Returns: (success, error_message)
        """
        try:
            # Read raw file
            with open(raw_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            # Detect header row by finding 'BodyType' or similar identifying column
            header_row = None
            for i, line in enumerate(lines):
                # Check if this row contains the expected identifying columns
                if 'BodyType' in line or 'Body Type' in line:
                    header_row = i
                    break

            if header_row is None:
                # Fallback: try to find row with 'Fuel' and quarter-like columns
                for i, line in enumerate(lines):
                    if 'Fuel' in line and re.search(r'\d{4}\s+Q[1-4]', line):
                        header_row = i
                        break

            if header_row is None:
                return False, "VEH0142: Could not identify table header safely"

            # Read data from detected header onwards
            df = pd.read_csv(raw_path, skiprows=header_row)

            # Verify required columns
            required_cols = ['BodyType', 'Fuel', 'Keepership', 'ONS Code']
            # Columns might have slight variations
            df_cols_lower = [c.lower().strip() for c in df.columns]
            req_cols_lower = [c.lower() for c in required_cols]

            if not all(any(req in col for req in req_cols_lower) for col in df_cols_lower):
                # Try alternate patterns
                if not any('bodytype' in col.lower() for col in df_cols_lower):
                    return False, f"VEH0142: Missing BodyType column"
                if not any('fuel' in col.lower() for col in df_cols_lower):
                    return False, f"VEH0142: Missing Fuel column"

            # Verify quarter columns
            quarter_cols = [c for c in df.columns if re.match(r'^\d{4}\s+Q[1-4]$', str(c))]
            if not quarter_cols:
                return False, "VEH0142: No quarter columns detected"

            # Save canonical
            df.to_csv(output_path, index=False)

            latest_quarter = self._detect_latest_quarter(quarter_cols)
            self.transformation_log.append({
                'file': 'VEH0142',
                'format_recognised': True,
                'normalisation': f'Removed {header_row} introductory rows',
                'rows_loaded': len(df),
                'latest_period': latest_quarter,
                'columns_detected': len(df.columns)
            })

            return True, None

        except Exception as e:
            return False, f"VEH0142: {str(e)}"

    def normalize_veh0125(self, raw_path: str, output_path: str) -> Tuple[bool, Optional[str]]:
        """Normalize VEH0125."""
        try:
            df = pd.read_csv(raw_path)

            # Detect header if needed
            quarter_cols = [c for c in df.columns if re.match(r'^\d{4}\s+Q[1-4]$', str(c))]
            if not quarter_cols:
                return False, "VEH0125: No quarter columns detected"

            df.to_csv(output_path, index=False)

            latest_quarter = self._detect_latest_quarter(quarter_cols)
            self.transformation_log.append({
                'file': 'VEH0125',
                'format_recognised': True,
                'normalisation': 'None required',
                'rows_loaded': len(df),
                'latest_period': latest_quarter,
                'columns_detected': len(df.columns)
            })

            return True, None
        except Exception as e:
            return False, f"VEH0125: {str(e)}"

    def normalize_veh0105(self, raw_path: str, output_path: str) -> Tuple[bool, Optional[str]]:
        """Normalize VEH0105."""
        try:
            df = pd.read_csv(raw_path)

            quarter_cols = [c for c in df.columns if re.match(r'^\d{4}\s+Q[1-4]$', str(c))]
            if not quarter_cols:
                return False, "VEH0105: No quarter columns detected"

            df.to_csv(output_path, index=False)

            latest_quarter = self._detect_latest_quarter(quarter_cols)
            self.transformation_log.append({
                'file': 'VEH0105',
                'format_recognised': True,
                'normalisation': 'None required',
                'rows_loaded': len(df),
                'latest_period': latest_quarter,
                'columns_detected': len(df.columns)
            })

            return True, None
        except Exception as e:
            return False, f"VEH0105: {str(e)}"

    def normalize_all_files(self, raw_dir: str, normalized_dir: str) -> Tuple[bool, str]:
        """
        Normalize all DfT files in raw_dir.

        Returns: (success, summary)
        """
        self.transformation_log = []

        os.makedirs(normalized_dir, exist_ok=True)

        file_handlers = {
            'df_VEH0135.csv': self.normalize_veh0135,
            'VEH0142.csv': self.normalize_veh0142,
            'df_VEH0125.csv': self.normalize_veh0125,
            'VEH0105.csv': self.normalize_veh0105,
        }

        all_success = True
        results = []

        for filename, handler in file_handlers.items():
            raw_path = os.path.join(raw_dir, filename)
            normalized_path = os.path.join(normalized_dir, filename)

            if not os.path.exists(raw_path):
                results.append(f"⚠ {filename}: Not found in upload")
                continue

            success, error = handler(raw_path, normalized_path)

            if success:
                results.append(f"✓ {filename}: Normalised")
            else:
                results.append(f"✗ {filename}: {error}")
                all_success = False

        # Determine latest common authoritative period
        periods = []
        for log in self.transformation_log:
            if log.get('latest_period'):
                periods.append(log['latest_period'])

        latest_common = min(periods) if periods else None

        summary = "\n".join(results)
        if latest_common:
            summary += f"\n\nLatest common authoritative period: {latest_common}"

        return all_success, summary

    def _detect_latest_quarter(self, quarter_cols: List[str]) -> Optional[str]:
        """
        Detect latest quarter from column names.

        Example: "2026 Q2" → "2026-06-30"
        """
        try:
            quarters = []
            for col in quarter_cols:
                match = re.match(r'^(\d{4})\s+Q([1-4])$', str(col))
                if match:
                    year = int(match.group(1))
                    quarter = int(match.group(2))
                    # Convert quarter to month-end date
                    month = (quarter - 1) * 3 + 3
                    quarters.append((year, month))

            if quarters:
                quarters.sort(reverse=True)
                year, month = quarters[0]
                # Determine day of month
                if month == 3:
                    day = 31
                elif month == 6:
                    day = 30
                elif month == 9:
                    day = 30
                else:  # month == 12
                    day = 31

                return f"{year:04d}-{month:02d}-{day:02d}"

        except Exception:
            pass

        return None

    def get_transformation_log(self) -> str:
        """Return formatted transformation log."""
        lines = ["DFT FILE INSPECTION"]
        lines.append("=" * 70)

        for log in self.transformation_log:
            lines.append(f"\n{log['file']}")
            lines.append(f"  Format recognised: {log['format_recognised']}")
            lines.append(f"  Normalisation: {log['normalisation']}")
            lines.append(f"  Rows loaded: {log['rows_loaded']}")
            lines.append(f"  Latest period: {log['latest_period']}")

        return "\n".join(lines)
