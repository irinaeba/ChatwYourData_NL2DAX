"""
Storage abstraction layer for NLtoDAX.

Provides a unified interface for loading schemas and prompts from either:
- Local filesystem (development)
- Azure Blob Storage (production)

The active backend is selected via the STORAGE_BACKEND env var:
  - "local" (default): reads from cache/schema/ and backend/prompts/
  - "azure": reads from Azure Blob Storage containers
"""

from backend.storage.base import StorageBackend, get_storage_backend
from backend.storage.version_manager import VersionManager

__all__ = ["StorageBackend", "get_storage_backend", "VersionManager"]
