"""Locations and fixed rules for C1 refresh candidates."""

import os
import re
import sys
from dataclasses import dataclass, field

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
C1_DIR = os.path.join(PROJECT_ROOT, "experiments", "ev_redistribution_c1")
RELEASE_DIR = os.path.join(C1_DIR, "release")
for p in (C1_DIR, RELEASE_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

# Approved design decisions (user, Phase 13A). Not configurable.
FIRST_QUARTER = "2025 Q1"            # C1 history starts March 2025 and accumulates: never a rolling window
LIVE_RELEASE_ID = "c1-20261014-ukpn-ev"
SOURCE_SET_SCHEMA = "c1-source-set/1"
CANDIDATE_SCHEMA = "c1-candidate/1"
INTEGRATION_NAMES = ("ev_actuals_dno.csv", "ev_actuals_la.csv", "ev_actuals_laep.csv")
LIVE_INTEGRATION_REL = "project/output_processed/integration"
PUBLISHED_C1_LSOA_REL = "experiments/ev_redistribution_c1/outputs/c1_lsoa_quarterly.csv"
LOOKUPS_REL = {"lsoa_dno": "lookups/LSOA to DNO.csv",
               "geography_lookup": "project/output_processed/integration/geography_lookup.csv"}
METHOD_CODE_REL = ("experiments/ev_redistribution_c1/c1_method.py", "experiments/ev_redistribution_c1/run_c1.py",
                   "experiments/ev_redistribution_c1/integration_writer.py")

QUARTER_RE = re.compile(r"^(\d{4}) Q([1-4])$")


def quarter_period(label):
    """'2026 Q2' -> '2026-06' (the integration period key)."""
    m = QUARTER_RE.match(label)
    return f"{m.group(1)}-{int(m.group(2)) * 3:02d}"


def quarters_through(latest_label, first=FIRST_QUARTER):
    """Expanding history: every quarter from March 2025 to the latest, oldest first."""
    def idx(l):
        m = QUARTER_RE.match(l)
        return int(m.group(1)) * 4 + int(m.group(2)) - 1
    a, b = idx(first), idx(latest_label)
    if b < a:
        raise ValueError(f"{latest_label} is before the C1 start quarter {first}")
    return [f"{i // 4} Q{i % 4 + 1}" for i in range(a, b + 1)]


def short_tmp():
    """Base for disposable environments: LCT_TMP if set, else a short per-user temp directory.

    Kept short on Windows (deep LFS/registry paths exceed MAX_PATH under long temp paths)."""
    import tempfile
    base = os.environ.get("LCT_TMP") or (os.path.join(os.environ["LOCALAPPDATA"], "Temp") if os.environ.get("LOCALAPPDATA")
                                         else tempfile.gettempdir())
    os.makedirs(base, exist_ok=True)
    return base


def expected_rows(n_quarters):
    """Integration row counts for an n-quarter C1 series (3 DNOs, 121 LAs, 14 LAEPs, BEV and PHEV)."""
    return {"ev_actuals_dno.csv": 6 * n_quarters, "ev_actuals_la.csv": 242 * n_quarters, "ev_actuals_laep.csv": 28 * n_quarters}


@dataclass(frozen=True)
class Workspace:
    root: str = field(default_factory=lambda: os.environ.get("LCT_C1_WORKSPACE", os.path.join(PROJECT_ROOT, "c1_candidates")))

    @property
    def registry_root(self):
        """Isolated candidate source registry (ingestion registry format)."""
        return os.path.join(self.root, "registry")

    def path(self, *parts):
        root = os.path.abspath(self.root)
        p = os.path.abspath(os.path.join(root, *parts))
        if os.path.commonpath([p, root]) != root:
            raise ValueError(f"path escapes the C1 workspace: {parts}")
        return p

    def ensure(self):
        for d in ("source_sets", "candidates", "baseline", "jobs"):
            os.makedirs(self.path(d), exist_ok=True)
        gi = self.path(".gitignore")
        if not os.path.exists(gi):
            with open(gi, "w", encoding="utf-8") as f:
                f.write("# C1 refresh candidates and their isolated source registry: large, never committed.\n*\n")
        return self
