"""
SQLite database for Data Manager persistence.

Tables:
  - smmt_entries: Raw SMMT monthly input (immutable history)
  - dft_dataset_versions: DfT upload metadata and validation
  - processing_history: Audit trail of operations
  - nowcast_estimates: Provisional geographic estimates (Phase 3+)
"""

import os
import sqlite3
import json
from datetime import datetime
from typing import Optional, List, Dict, Any

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "project",
    "data_manager",
    "database.db"
)


class Database:
    """SQLite database operations for Data Manager."""

    def __init__(self, db_path: str = DB_PATH):
        """Initialize database connection."""
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.init_schema()

    def get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self):
        """Initialize database schema."""
        conn = self.get_connection()
        cursor = conn.cursor()

        # SMMT Monthly Input History (with versioning for corrections)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS smmt_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                bev_cars INTEGER NOT NULL,
                bev_vans INTEGER NOT NULL,
                phev_cars INTEGER NOT NULL,
                phev_vans INTEGER NOT NULL,
                entered_by TEXT,
                entered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'ACTIVE',
                notes TEXT,
                is_current BOOLEAN DEFAULT 1,
                corrected_from_id INTEGER,
                corrected_at TIMESTAMP
            )
        """)

        # DfT Dataset Versions
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS dft_dataset_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_name TEXT NOT NULL,
                period_end TEXT NOT NULL,
                upload_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                uploaded_by TEXT,
                file_path TEXT NOT NULL,
                validation_status TEXT,
                validation_errors TEXT,
                is_production BOOLEAN DEFAULT 0,
                promoted_at TIMESTAMP,
                UNIQUE(dataset_name, period_end)
            )
        """)

        # Processing History
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS processing_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation TEXT NOT NULL,
                dft_version_id INTEGER REFERENCES dft_dataset_versions(id),
                input_summary TEXT,
                output_summary TEXT,
                status TEXT,
                error_message TEXT,
                performed_by TEXT,
                performed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Nowcast Estimates (Phase 3+)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS nowcast_estimates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                dno TEXT NOT NULL,
                bev_cars INTEGER,
                bev_vans INTEGER,
                phev_cars INTEGER,
                phev_vans INTEGER,
                source TEXT NOT NULL,
                dft_weight_vintage TEXT,
                is_current BOOLEAN DEFAULT 1,
                superseded_at TIMESTAMP,
                processing_vintage TIMESTAMP,
                UNIQUE(year, month, dno, source)
            )
        """)

        conn.commit()
        conn.close()

    # ========== SMMT Operations ==========

    def add_smmt_entry(
        self,
        year: int,
        month: int,
        bev_cars: int,
        bev_vans: int,
        phev_cars: int,
        phev_vans: int,
        entered_by: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> int:
        """Add or update SMMT entry. Returns entry ID."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO smmt_entries
                (year, month, bev_cars, bev_vans, phev_cars, phev_vans, entered_by, notes, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE')
            """, (year, month, bev_cars, bev_vans, phev_cars, phev_vans, entered_by, notes))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def get_smmt_entries(self, year: Optional[int] = None, month: Optional[int] = None) -> List[Dict]:
        """Query SMMT entries."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            query = "SELECT * FROM smmt_entries WHERE 1=1"
            params = []
            if year is not None:
                query += " AND year = ?"
                params.append(year)
            if month is not None:
                query += " AND month = ?"
                params.append(month)
            query += " ORDER BY year DESC, month DESC"

            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_latest_smmt_entry(self) -> Optional[Dict]:
        """Get most recent SMMT entry."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT * FROM smmt_entries
                ORDER BY year DESC, month DESC
                LIMIT 1
            """)
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_current_smmt_entry(self, year: int, month: int) -> Optional[Dict]:
        """Get current (active) SMMT entry for a month."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT * FROM smmt_entries
                WHERE year = ? AND month = ? AND is_current = 1
                LIMIT 1
            """, (year, month))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def correct_smmt_entry(
        self,
        year: int,
        month: int,
        bev_cars: int,
        bev_vans: int,
        phev_cars: int,
        phev_vans: int,
        notes: Optional[str] = None,
    ) -> int:
        """Correct an existing SMMT entry. Preserves version history."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            # Get current entry
            cursor.execute("""
                SELECT id FROM smmt_entries
                WHERE year = ? AND month = ? AND is_current = 1
            """, (year, month))
            current_row = cursor.fetchone()

            if current_row:
                old_id = current_row[0]
                # Mark old as not current
                cursor.execute("""
                    UPDATE smmt_entries
                    SET is_current = 0
                    WHERE id = ?
                """, (old_id,))

                # Create new corrected version
                cursor.execute("""
                    INSERT INTO smmt_entries
                    (year, month, bev_cars, bev_vans, phev_cars, phev_vans, status, is_current, corrected_from_id, corrected_at, notes)
                    VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', 1, ?, ?, ?)
                """, (year, month, bev_cars, bev_vans, phev_cars, phev_vans, old_id, datetime.utcnow().isoformat() + "Z", notes))

                conn.commit()
                return cursor.lastrowid
            else:
                # No existing entry, just insert new
                return self.add_smmt_entry(year, month, bev_cars, bev_vans, phev_cars, phev_vans, notes=notes)
        finally:
            conn.close()

    def get_smmt_entry_history(self, year: int, month: int) -> List[Dict]:
        """Get all versions of an SMMT entry (including corrections)."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT * FROM smmt_entries
                WHERE year = ? AND month = ?
                ORDER BY entered_at ASC
            """, (year, month))
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def update_smmt_status(self, year: int, month: int, status: str, superseded_at: Optional[str] = None):
        """Update SMMT entry status (e.g., mark as SUPERSEDED BY DfT)."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            if superseded_at is None:
                superseded_at = datetime.utcnow().isoformat() + "Z"
            cursor.execute("""
                UPDATE smmt_entries
                SET status = ?
                WHERE year = ? AND month = ? AND is_current = 1
            """, (status, year, month))
            conn.commit()
        finally:
            conn.close()

    def get_current_smmt_entries_for_history(self) -> List[Dict]:
        """Get all current (active) SMMT entries for the history table."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT * FROM smmt_entries
                WHERE is_current = 1
                ORDER BY year DESC, month DESC
            """)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    # ========== DfT Operations ==========

    def add_dft_version(
        self,
        dataset_name: str,
        period_end: str,
        file_path: str,
        uploaded_by: Optional[str] = None,
        validation_status: str = "PENDING",
        validation_errors: Optional[List[str]] = None,
    ) -> int:
        """Record DfT dataset upload. Returns version ID."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            errors_json = json.dumps(validation_errors or [])
            cursor.execute("""
                INSERT INTO dft_dataset_versions
                (dataset_name, period_end, file_path, uploaded_by, validation_status, validation_errors)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (dataset_name, period_end, file_path, uploaded_by, validation_status, errors_json))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def get_dft_versions(self, dataset_name: Optional[str] = None) -> List[Dict]:
        """Query DfT dataset versions."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            query = "SELECT * FROM dft_dataset_versions WHERE 1=1"
            params = []
            if dataset_name:
                query += " AND dataset_name = ?"
                params.append(dataset_name)
            query += " ORDER BY period_end DESC"

            cursor.execute(query, params)
            results = []
            for row in cursor.fetchall():
                d = dict(row)
                d["validation_errors"] = json.loads(d.get("validation_errors", "[]"))
                results.append(d)
            return results
        finally:
            conn.close()

    def get_latest_dft_version(self, dataset_name: str, production_only: bool = True) -> Optional[Dict]:
        """Get most recent DfT version for a dataset."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            query = "SELECT * FROM dft_dataset_versions WHERE dataset_name = ?"
            params = [dataset_name]
            if production_only:
                query += " AND is_production = 1"
            query += " ORDER BY period_end DESC LIMIT 1"

            cursor.execute(query, params)
            row = cursor.fetchone()
            if row:
                d = dict(row)
                d["validation_errors"] = json.loads(d.get("validation_errors", "[]"))
                return d
            return None
        finally:
            conn.close()

    def mark_dft_as_production(self, version_id: int):
        """Mark DfT version as production."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                UPDATE dft_dataset_versions
                SET is_production = 1, promoted_at = ?
                WHERE id = ?
            """, (datetime.utcnow().isoformat() + "Z", version_id))
            conn.commit()
        finally:
            conn.close()

    # ========== Processing History ==========

    def add_processing_record(
        self,
        operation: str,
        status: str,
        dft_version_id: Optional[int] = None,
        input_summary: Optional[Dict] = None,
        output_summary: Optional[Dict] = None,
        error_message: Optional[str] = None,
        performed_by: Optional[str] = None,
    ) -> int:
        """Record processing operation."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            input_json = json.dumps(input_summary or {})
            output_json = json.dumps(output_summary or {})
            cursor.execute("""
                INSERT INTO processing_history
                (operation, dft_version_id, input_summary, output_summary, status, error_message, performed_by)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (operation, dft_version_id, input_json, output_json, status, error_message, performed_by))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def get_processing_history(self, limit: int = 50) -> List[Dict]:
        """Get recent processing history."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT * FROM processing_history
                ORDER BY performed_at DESC
                LIMIT ?
            """, (limit,))
            results = []
            for row in cursor.fetchall():
                d = dict(row)
                d["input_summary"] = json.loads(d.get("input_summary", "{}"))
                d["output_summary"] = json.loads(d.get("output_summary", "{}"))
                results.append(d)
            return results
        finally:
            conn.close()
