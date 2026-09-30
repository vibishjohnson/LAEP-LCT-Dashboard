"""
DfT Inspector — Detect current authoritative DfT state from existing pipeline outputs.

Inspects:
  - scripts/01_ev_processing.py output (lsoa_ev_bodytype.csv)
  - DfT source files in ev/ directory
  - Current production EV outputs

Determines:
  - Latest authoritative DfT quarter end date
  - Which VEH tables are present and valid
  - Current EV processing state
"""

import os
import re
import pandas as pd
from datetime import datetime
from typing import Optional, Dict, Tuple


class DftInspector:
    """Inspect current DfT state from existing pipeline."""

    def __init__(self, project_root: str = None):
        """Initialize inspector."""
        if project_root is None:
            project_root = os.path.dirname(os.path.dirname(__file__))
        self.project_root = project_root
        self.ev_dir = os.path.join(project_root, "ev")
        self.output_dir = os.path.join(project_root, "project", "output_processed")

    def get_dft_tables(self) -> Dict[str, bool]:
        """Check which DfT tables are present."""
        tables = {
            "VEH0135": os.path.exists(os.path.join(self.ev_dir, "df_VEH0135.csv")),
            "VEH0125": os.path.exists(os.path.join(self.ev_dir, "df_VEH0125.csv")),
            "VEH0105": os.path.exists(os.path.join(self.ev_dir, "VEH0105.csv")),
            "VEH0142": os.path.exists(os.path.join(self.ev_dir, "VEH0142.csv")),
        }
        return tables

    def detect_latest_dft_quarter(self) -> Optional[str]:
        """
        Detect latest authoritative DfT quarter from VEH0135.

        Returns: Quarter-end date as 'YYYY-MM-DD' or None if unable to detect.
        """
        veh0135_path = os.path.join(self.ev_dir, "df_VEH0135.csv")
        if not os.path.exists(veh0135_path):
            return None

        try:
            # Read header to detect quarter columns
            df = pd.read_csv(veh0135_path, nrows=1)
            quarter_re = re.compile(r"^(\d{4})\sQ([1-4])$")

            quarters = []
            for col in df.columns:
                match = quarter_re.match(str(col).strip())
                if match:
                    year = int(match.group(1))
                    q = int(match.group(2))
                    # Convert quarter to month-end date
                    month_day = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}[q]
                    period = f"{year}-{month_day}"
                    quarters.append(period)

            if not quarters:
                return None

            # Return latest (sorted alphabetically works for YYYY-MM-DD)
            return max(quarters)
        except Exception as e:
            print(f"Error detecting DfT quarter: {e}")
            return None

    def get_current_ev_state(self) -> Dict:
        """Get current EV processing state from production outputs."""
        state = {
            "latest_dft_quarter": self.detect_latest_dft_quarter(),
            "dft_tables_present": self.get_dft_tables(),
            "ev_outputs_exist": {},
            "latest_ev_period": None,
        }

        # Check output files
        output_files = {
            "lsoa_ev_bodytype.csv": os.path.join(self.output_dir, "lsoa_ev_bodytype.csv"),
            "ev_actuals_dno.csv": os.path.join(self.output_dir, "ev_actuals_dno.csv"),
            "ev_actuals_lsoa.csv": os.path.join(self.output_dir, "ev_actuals_lsoa.csv"),
            "dashboard_data_dno.csv": os.path.join(self.output_dir, "dashboard_data_dno.csv"),
        }

        for name, path in output_files.items():
            state["ev_outputs_exist"][name] = os.path.exists(path)

        # Try to detect latest period in EV outputs
        try:
            dashboard_path = output_files["dashboard_data_dno.csv"]
            if os.path.exists(dashboard_path):
                df = pd.read_csv(dashboard_path, usecols=["period", "tech_type"], nrows=1000)
                ev_periods = df[df["tech_type"] == "EV"]["period"].unique()
                if len(ev_periods) > 0:
                    state["latest_ev_period"] = str(max(ev_periods))
        except Exception as e:
            print(f"Error detecting latest EV period: {e}")

        return state

    def get_inspection_report(self) -> str:
        """Generate human-readable inspection report."""
        state = self.get_current_ev_state()

        report = []
        report.append("=" * 70)
        report.append("DfT INSPECTION REPORT")
        report.append("=" * 70)
        report.append(f"Generated: {datetime.now().isoformat()}")
        report.append("")

        # DfT Table Status
        report.append("DfT TABLE STATUS:")
        tables = state["dft_tables_present"]
        for table, present in tables.items():
            status = "✓" if present else "✗"
            report.append(f"  {status} {table}")
        report.append("")

        # Latest DfT Quarter
        report.append("LATEST AUTHORITATIVE DfT:")
        latest_quarter = state["latest_dft_quarter"]
        if latest_quarter:
            # Parse date to quarter
            parts = latest_quarter.split("-")
            if parts[1:] == ["03", "31"]:
                q_label = "Q1"
            elif parts[1:] == ["06", "30"]:
                q_label = "Q2"
            elif parts[1:] == ["09", "30"]:
                q_label = "Q3"
            elif parts[1:] == ["12", "31"]:
                q_label = "Q4"
            else:
                q_label = "?"
            report.append(f"  Quarter: {parts[0]} {q_label}")
            report.append(f"  Period: {latest_quarter}")
        else:
            report.append("  Unable to detect DfT quarter from VEH0135")
        report.append("")

        # EV Output Status
        report.append("EV PROCESSING OUTPUTS:")
        outputs = state["ev_outputs_exist"]
        for name, exists in outputs.items():
            status = "✓" if exists else "✗"
            report.append(f"  {status} {name}")
        report.append("")

        # Latest EV Period
        report.append("LATEST EV OBSERVATION IN DASHBOARD:")
        if state["latest_ev_period"]:
            report.append(f"  Period: {state['latest_ev_period']}")
        else:
            report.append("  Unable to detect latest period")
        report.append("")

        report.append("=" * 70)
        return "\n".join(report)


if __name__ == "__main__":
    inspector = DftInspector()
    print(inspector.get_inspection_report())
