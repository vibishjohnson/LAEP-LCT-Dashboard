"""Machine-specific locations, resolved at run time (no hardcoded personal paths in the C1 code).

The only external location the C1 tooling refers to is the operational dashboard_integration runner. It is taken from:
  1. the environment variable C1_RUNNER_DIR, if set; otherwise
  2. "operational_runner_dir" in integration_runner/local_paths.json (git-ignored; see local_paths.example.json).
Tests that need the operational runner or its outputs skip when the folder is not present on the machine.
"""
import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def runner_dir():
    """Folder of the operational dashboard_integration package, or None if it cannot be determined."""
    env = os.environ.get('C1_RUNNER_DIR')
    if env:
        return Path(env)
    try:
        local = json.loads((REPO / 'integration_runner' / 'local_paths.json').read_text(encoding='utf-8'))
        return Path(local['operational_runner_dir']) if local.get('operational_runner_dir') else None
    except (OSError, ValueError, AttributeError):
        return None


def runner_file(name):
    d = runner_dir()
    return d / name if d else Path('__runner_not_configured__') / name
