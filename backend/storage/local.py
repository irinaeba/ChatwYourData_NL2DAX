"""
Local filesystem storage backend (development).

Reads schemas from cache/schema/ and prompts from backend/prompts/
exactly as the app currently works — zero behavior change for local dev.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, List

from backend.storage.base import StorageBackend

logger = logging.getLogger(__name__)

_project_root = Path(__file__).resolve().parent.parent.parent


class LocalStorageBackend(StorageBackend):
    """Reads schemas and prompts from the local filesystem."""

    def __init__(self):
        self.schema_dir = _project_root / "cache" / "schema"
        self.prompts_dir = _project_root / "backend" / "prompts"

    def get_schema(self, domain: str, version: Optional[str] = None) -> str:
        """Load schema from cache/schema/schema_{domain}.txt"""
        if version:
            filename = f"schema_{domain}_{version}.txt"
        else:
            filename = f"schema_{domain}.txt"

        schema_path = self.schema_dir / filename
        if not schema_path.exists():
            raise FileNotFoundError(f"Schema file not found: {schema_path}")

        return schema_path.read_text(encoding="utf-8")

    def get_prompt(self, prompt_path: str, version: Optional[str] = None) -> str:
        """
        Load prompt from backend/prompts/{prompt_path}.

        prompt_path examples:
          - "prompt_generator/dax_generator_prompt_work_orders.py"
          - "prompt_validator/dax_validator_global_instructions.py"
          - "query_planner_prompt.py"
        """
        full_path = self.prompts_dir / prompt_path
        if not full_path.exists():
            raise FileNotFoundError(f"Prompt file not found: {full_path}")

        return full_path.read_text(encoding="utf-8")

    def list_schema_versions(self) -> List[Dict]:
        """List dated schema versions found in cache/schema/."""
        versions = set()
        for f in self.schema_dir.glob("schema_pack_*.txt"):
            # Extract date from schema_pack_2026-06-26.txt
            date_part = f.stem.replace("schema_pack_", "")
            versions.add(date_part)

        return sorted(
            [{"version": v, "source": "local"} for v in versions],
            key=lambda x: x["version"],
            reverse=True,
        )

    def list_prompt_versions(self) -> List[Dict]:
        """Local prompts don't have versioning — return a single 'current' entry."""
        return [{"version": "current", "source": "local"}]

    def get_active_schema_version(self) -> str:
        """Local always uses the canonical (non-dated) files."""
        return "current"

    def get_active_prompt_version(self) -> str:
        return "current"
