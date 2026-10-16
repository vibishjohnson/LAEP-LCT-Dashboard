"""
Ingestion limits and locations. Every value can be overridden with an environment variable
(read once, when the configuration object is created) or by passing a Settings object.
"""

import os
from dataclasses import dataclass, field

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MB = 1024 * 1024


def _env_int(name, default):
    value = os.environ.get(name)
    return int(value) if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    # Where the registry lives. Outside project/ so the C1 release protected snapshot does not hash uploads.
    registry_root: str = field(default_factory=lambda: os.environ.get(
        "LCT_SOURCE_REGISTRY", os.path.join(PROJECT_ROOT, "source_registry")))
    # Read-only production inputs, used only for comparison.
    production_ev_dir: str = os.path.join(PROJECT_ROOT, "ev")
    lsoa_dno_lookup: str = os.path.join(PROJECT_ROOT, "lookups", "LSOA to DNO.csv")

    # Upload limits. The largest real DfT source seen (df_VEH0125.csv, 2026 Q2) is 245 MB.
    max_upload_bytes: int = field(default_factory=lambda: _env_int("LCT_INGEST_MAX_UPLOAD_MB", 1024) * MB)
    upload_chunk_bytes: int = 1 * MB
    max_concurrent_uploads: int = 2
    max_queued_jobs: int = 4

    # Spreadsheet container limits (zip-bomb protection). Real VEH0105 ODS: content.xml 412 MB, ratio 23.
    max_archive_members: int = 200
    max_member_uncompressed_bytes: int = field(default_factory=lambda: _env_int("LCT_INGEST_MAX_XML_MB", 2048) * MB)
    max_total_uncompressed_bytes: int = field(default_factory=lambda: _env_int("LCT_INGEST_MAX_XML_MB", 2048) * MB + 64 * MB)
    max_compression_ratio: float = 200.0
    max_columns: int = 2048
    max_repeated_rows: int = 100_000      # a non-blank row repeated more often than this is rejected
    max_data_rows: int = 5_000_000

    # Worker process limits.
    worker_memory_limit_mb: int = field(default_factory=lambda: _env_int("LCT_INGEST_WORKER_MEMORY_MB", 1536))
    worker_timeout_seconds: int = field(default_factory=lambda: _env_int("LCT_INGEST_WORKER_TIMEOUT_S", 1800))
    preview_rows: int = 20
    # Keep the original bytes of a failed parse for diagnosis when no larger than this.
    keep_failed_upload_bytes: int = 300 * MB
    # Compare candidates against production (read-only). Disabled in some tests.
    compare_with_production: bool = True


def default_settings() -> Settings:
    return Settings()
