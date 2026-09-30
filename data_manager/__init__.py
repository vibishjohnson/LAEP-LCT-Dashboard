"""
LCT Data Manager — Private EV data ingestion and processing tool

Phase 1: Foundation + DfT vintage detection
"""

from data_manager.db import Database
from data_manager.dft_inspector import DftInspector

__version__ = "0.1.0"
__all__ = ["Database", "DftInspector"]
