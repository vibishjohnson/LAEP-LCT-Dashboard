"""
SMMT Monthly EV Registration Input

Handles:
- Monthly form submission validation
- Duplicate detection
- Correction workflow
- Next month suggestion based on DfT coverage
"""

from typing import Optional, Tuple, List
from datetime import datetime, date
from data_manager.db import Database
from data_manager.dft_inspector import DftInspector


class SMmtManager:
    """Manage SMMT monthly input and history."""

    def __init__(self, db_path: Optional[str] = None):
        self.db = Database(db_path) if db_path else Database()
        self.inspector = DftInspector()

    def get_latest_authoritative_dft_period(self) -> Optional[Tuple[int, int]]:
        """Get latest DfT quarter as (year, month) tuple."""
        period_str = self.inspector.detect_latest_dft_quarter()
        if not period_str:
            return None
        # Parse "2026-03-31" → (2026, 3)
        parts = period_str.split("-")
        year = int(parts[0])
        month = int(parts[1])
        return (year, month)

    def get_latest_smmt_entry(self) -> Optional[Tuple[int, int]]:
        """Get latest SMMT entry as (year, month) tuple."""
        entry = self.db.get_latest_smmt_entry()
        if not entry:
            return None
        return (entry["year"], entry["month"])

    def get_current_smmt_months(self) -> List[Tuple[int, int]]:
        """Get all currently active SMMT months as sorted list."""
        entries = self.db.get_current_smmt_entries_for_history()
        months = [(e["year"], e["month"]) for e in entries]
        return sorted(months, reverse=True)

    def suggest_next_month(self) -> Optional[Tuple[int, int]]:
        """Suggest the next month to enter SMMT data for.

        Logic:
        1. Find latest authoritative DfT period
        2. Iterate months after that until finding a gap or end of current data
        3. Suggest the first month that hasn't been entered yet
        4. If all months are filled, suggest next month after latest SMMT entry
        """
        dft_period = self.get_latest_authoritative_dft_period()
        if not dft_period:
            return None

        dft_year, dft_month = dft_period
        current_months = set(self.get_current_smmt_months())

        # Start from month after DfT and find first gap
        year, month = dft_year, dft_month
        for _ in range(24):  # Look ahead 24 months
            # Increment month
            month += 1
            if month > 12:
                month = 1
                year += 1

            if (year, month) not in current_months:
                return (year, month)

        return None

    def validate_smmt_input(
        self,
        year: int,
        month: int,
        bev_cars: int,
        bev_vans: int,
        phev_cars: int,
        phev_vans: int,
    ) -> Tuple[bool, Optional[str]]:
        """Validate SMMT input. Returns (is_valid, error_message)."""
        # Month validation
        if not (1 <= month <= 12):
            return False, "Month must be between 1 and 12"

        # Year validation (allow reasonable range)
        current_year = datetime.now().year
        if not (2020 <= year <= current_year + 2):
            return False, f"Year must be between 2020 and {current_year + 2}"

        # Numeric validation
        values = [bev_cars, bev_vans, phev_cars, phev_vans]
        for val in values:
            if val < 0:
                return False, "Values must not be negative"

        return True, None

    def check_existing_entry(self, year: int, month: int) -> Optional[dict]:
        """Check if entry exists for this month. Returns existing entry or None."""
        return self.db.get_current_smmt_entry(year, month)

    def add_smmt_entry(
        self,
        year: int,
        month: int,
        bev_cars: int,
        bev_vans: int,
        phev_cars: int,
        phev_vans: int,
        notes: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Add new SMMT entry. Returns (success, message)."""
        # Validate
        is_valid, error = self.validate_smmt_input(year, month, bev_cars, bev_vans, phev_cars, phev_vans)
        if not is_valid:
            return False, error

        # Check for existing entry
        existing = self.check_existing_entry(year, month)
        if existing:
            return False, f"Entry already exists for {self.month_name(month)} {year}. Use 'Correct Existing Entry' to modify it."

        # Insert
        try:
            entry_id = self.db.add_smmt_entry(year, month, bev_cars, bev_vans, phev_cars, phev_vans, notes=notes)
            return True, f"Successfully added SMMT entry for {self.month_name(month)} {year}"
        except Exception as e:
            return False, f"Database error: {str(e)}"

    def correct_smmt_entry(
        self,
        year: int,
        month: int,
        bev_cars: int,
        bev_vans: int,
        phev_cars: int,
        phev_vans: int,
        notes: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Correct an existing SMMT entry. Preserves version history."""
        # Validate
        is_valid, error = self.validate_smmt_input(year, month, bev_cars, bev_vans, phev_cars, phev_vans)
        if not is_valid:
            return False, error

        try:
            entry_id = self.db.correct_smmt_entry(year, month, bev_cars, bev_vans, phev_cars, phev_vans, notes=notes)
            return True, f"Successfully corrected SMMT entry for {self.month_name(month)} {year}. Previous version retained in history."
        except Exception as e:
            return False, f"Database error: {str(e)}"

    def get_smmt_history(self) -> List[dict]:
        """Get all current SMMT entries for display in history table."""
        return self.db.get_current_smmt_entries_for_history()

    def get_entry_versions(self, year: int, month: int) -> List[dict]:
        """Get all versions of an entry (including corrections)."""
        return self.db.get_smmt_entry_history(year, month)

    def format_value(self, value: int) -> str:
        """Format integer value with thousands separator."""
        return f"{value:,}"

    def parse_value(self, value_str: str) -> Optional[int]:
        """Parse formatted value string (with commas) to integer."""
        if not value_str:
            return None
        try:
            return int(value_str.replace(",", ""))
        except ValueError:
            return None

    @staticmethod
    def month_name(month: int) -> str:
        """Convert month number to name."""
        names = {
            1: "January", 2: "February", 3: "March", 4: "April",
            5: "May", 6: "June", 7: "July", 8: "August",
            9: "September", 10: "October", 11: "November", 12: "December"
        }
        return names.get(month, "Unknown")

    @staticmethod
    def month_short(month: int) -> str:
        """Convert month number to short name."""
        names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        return names[month - 1] if 1 <= month <= 12 else "?"
