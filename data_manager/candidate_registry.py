"""
Candidate Registry

Maps run_id to candidate metadata and directory path.
Supports multiple candidate types: SMMT nowcast, DfT updates, etc.
"""

import os
import json
from datetime import datetime
from typing import Dict, Optional, Tuple

class CandidateRegistry:
    """Registry of promotion candidates."""

    def __init__(self, project_root: str):
        self.project_root = project_root
        self.registry_file = os.path.join(
            project_root, "project", "dft_staging", "candidate_registry.json"
        )
        self._ensure_registry()

    def _ensure_registry(self):
        """Ensure registry file exists."""
        registry_dir = os.path.dirname(self.registry_file)
        os.makedirs(registry_dir, exist_ok=True)

        if not os.path.exists(self.registry_file):
            # Initialize with empty registry
            self._write_registry({})

    def _read_registry(self) -> Dict:
        """Read current registry."""
        try:
            with open(self.registry_file) as f:
                return json.load(f)
        except:
            return {}

    def _write_registry(self, registry: Dict):
        """Write registry to file."""
        with open(self.registry_file, 'w') as f:
            json.dump(registry, f, indent=2)

    def register_candidate(
        self,
        run_id: str,
        candidate_type: str,
        candidate_dir: str,
        metadata: Dict
    ) -> bool:
        """
        Register a new candidate.

        Args:
            run_id: Unique run identifier
            candidate_type: 'nowcast' or 'dft_update'
            candidate_dir: Relative path to candidate directory
            metadata: Candidate metadata

        Returns: Success status
        """
        try:
            registry = self._read_registry()

            registry[run_id] = {
                "type": candidate_type,
                "directory": candidate_dir,
                "created_at": datetime.now().isoformat(),
                "metadata": metadata
            }

            self._write_registry(registry)
            return True
        except Exception as e:
            print(f"Error registering candidate: {e}")
            return False

    def get_candidate(self, run_id: str) -> Optional[Dict]:
        """Get candidate info by run_id."""
        registry = self._read_registry()
        return registry.get(run_id)

    def get_candidate_directory(self, run_id: str) -> Optional[str]:
        """Get candidate directory path."""
        candidate = self.get_candidate(run_id)
        if candidate:
            return os.path.join(self.project_root, candidate["directory"])
        return None

    def get_candidate_type(self, run_id: str) -> Optional[str]:
        """Get candidate type (nowcast or dft_update)."""
        candidate = self.get_candidate(run_id)
        if candidate:
            return candidate.get("type")
        return None

    def get_candidate_metadata(self, run_id: str) -> Optional[Dict]:
        """Get candidate metadata."""
        candidate = self.get_candidate(run_id)
        if candidate:
            return candidate.get("metadata", {})
        return None

    def list_candidates(self) -> Dict:
        """List all registered candidates."""
        return self._read_registry()

# SMMT nowcast candidates are not registered here: each one is a sealed folder under
# project/dft_staging/smmt_candidates/ (see smmt_candidate_builder.py), and every figure shown in the data manager
# is read from that candidate's manifest. Entries already in candidate_registry.json are legacy admin-promotion runs.
