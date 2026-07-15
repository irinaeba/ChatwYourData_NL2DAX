"""
NLtoDAX Admin Portal — Backend

FastAPI application for managing prompts and schemas:
- CRUD operations for prompt versions
- CRUD operations for schema versions
- Trigger schema extraction jobs
- Set active versions (deploy live)
- Notify main app to reload caches
"""

import os
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, List
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import httpx

# Add project root to path for shared imports
import sys
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from backend.storage.version_manager import VersionManager

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(title="NLtoDAX Admin Portal", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Config ──────────────────────────────────────────────────
MAIN_APP_URL = os.environ.get("MAIN_APP_URL", "http://localhost:8000")


# ─── Pydantic Models ────────────────────────────────────────

class CreateVersionRequest(BaseModel):
    version: str = Field(..., description="Version identifier (e.g., 'v5' or '2026-07-03_v1')")
    files: Dict[str, str] = Field(..., description="Dict of {filename: content}")
    author: Optional[str] = None
    changelog: Optional[str] = None


class SetActiveRequest(BaseModel):
    version: str = Field(..., description="Version to set as active")


class TriggerExtractionRequest(BaseModel):
    """Optional parameters for schema extraction."""
    pass  # Future: domain filters, custom config, etc.


class UpdateFileRequest(BaseModel):
    version: str = Field(..., description="Version containing the file")
    file_path: str = Field(..., description="Relative file path within the version")
    content: str = Field(..., description="New file content")


# ─── Storage Manager ────────────────────────────────────────

_version_manager: Optional[VersionManager] = None


def get_version_manager() -> VersionManager:
    global _version_manager
    if _version_manager is None:
        _version_manager = VersionManager()
    return _version_manager


# ─── Prompt Endpoints ───────────────────────────────────────

@app.get("/api/prompts/versions")
async def list_prompt_versions():
    """List all available prompt versions."""
    from backend.storage import get_storage_backend
    storage = get_storage_backend()
    versions = storage.list_prompt_versions()
    return {"versions": versions}


@app.get("/api/prompts/versions/{version}")
async def get_prompt_version(version: str):
    """Get all files for a specific prompt version."""
    try:
        vm = get_version_manager()
        files = vm.get_prompt_version_files(version)
        return {"version": version, "files": files}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/prompts/active")
async def get_active_prompt_version():
    """Get the currently active prompt version."""
    from backend.storage import get_storage_backend
    storage = get_storage_backend()
    try:
        version = storage.get_active_prompt_version()
        return {"version": version}
    except Exception:
        return {"version": None, "message": "No active version set"}


@app.post("/api/prompts/versions")
async def create_prompt_version(request: CreateVersionRequest):
    """Create a new prompt version."""
    try:
        vm = get_version_manager()
        metadata = {}
        if request.author:
            metadata["author"] = request.author
        if request.changelog:
            metadata["changelog"] = request.changelog

        manifest = vm.create_prompt_version(
            version=request.version,
            files=request.files,
            metadata=metadata,
        )
        return {"status": "created", "manifest": manifest}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/prompts/active")
async def set_active_prompt_version(request: SetActiveRequest):
    """Set the active prompt version and notify the main app."""
    try:
        vm = get_version_manager()
        vm.set_active_prompt_version(request.version)

        # Notify main app to reload
        await _notify_main_app_reload()

        return {"status": "ok", "active_version": request.version}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Schema Endpoints ───────────────────────────────────────

@app.get("/api/schemas/versions")
async def list_schema_versions():
    """List all available schema versions."""
    from backend.storage import get_storage_backend
    storage = get_storage_backend()
    versions = storage.list_schema_versions()
    return {"versions": versions}


@app.get("/api/schemas/versions/{version}")
async def get_schema_version(version: str):
    """Get all files for a specific schema version."""
    try:
        vm = get_version_manager()
        files = vm.get_schema_version_files(version)
        return {"version": version, "files": files}
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/schemas/active")
async def get_active_schema_version():
    """Get the currently active schema version."""
    from backend.storage import get_storage_backend
    storage = get_storage_backend()
    try:
        version = storage.get_active_schema_version()
        return {"version": version}
    except Exception:
        return {"version": None, "message": "No active version set"}


@app.post("/api/schemas/versions")
async def create_schema_version(request: CreateVersionRequest):
    """Create a new schema version (manual upload)."""
    try:
        vm = get_version_manager()
        metadata = {}
        if request.author:
            metadata["author"] = request.author
        if request.changelog:
            metadata["changelog"] = request.changelog

        manifest = vm.create_schema_version(
            version=request.version,
            files=request.files,
            metadata=metadata,
        )
        return {"status": "created", "manifest": manifest}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/schemas/active")
async def set_active_schema_version(request: SetActiveRequest):
    """Set the active schema version and notify the main app."""
    try:
        vm = get_version_manager()
        vm.set_active_schema_version(request.version)

        # Notify main app to reload
        await _notify_main_app_reload()

        return {"status": "ok", "active_version": request.version}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Schema Extraction ──────────────────────────────────────

@app.post("/api/schemas/extract")
async def trigger_schema_extraction(request: TriggerExtractionRequest = None):
    """
    Trigger schema extraction job.
    In Azure: starts a Container Apps Job.
    Locally: runs the extraction script inline.
    """
    environment = os.environ.get("ENVIRONMENT", "local")

    if environment == "production":
        return await _trigger_azure_extraction_job()
    else:
        return await _run_local_extraction()


@app.get("/api/schemas/extract/status")
async def get_extraction_status():
    """Get the status of the last schema extraction job."""
    # In production, this would poll the Container Apps Job status
    return {"status": "idle", "message": "No extraction in progress"}


# ─── File Update Endpoint ───────────────────────────────────

@app.put("/api/files")
async def update_file(request: UpdateFileRequest):
    """Update a single file within a version (creates a new version)."""
    try:
        vm = get_version_manager()

        # Determine if it's a prompt or schema based on file path
        if "generator" in request.file_path or "validator" in request.file_path or "planner" in request.file_path:
            files = vm.get_prompt_version_files(request.version)
            files[request.file_path] = request.content

            # Create as new version with _edited suffix
            new_version = f"{request.version}_edited_{datetime.now(timezone.utc).strftime('%H%M%S')}"
            manifest = vm.create_prompt_version(version=new_version, files=files)
        else:
            files = vm.get_schema_version_files(request.version)
            files[request.file_path] = request.content

            new_version = f"{request.version}_edited_{datetime.now(timezone.utc).strftime('%H%M%S')}"
            manifest = vm.create_schema_version(version=new_version, files=files)

        return {"status": "created", "new_version": new_version, "manifest": manifest}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Internal Helpers ───────────────────────────────────────

async def _notify_main_app_reload():
    """Call the main app's /admin/reload endpoint to invalidate its cache."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(f"{MAIN_APP_URL}/admin/reload")
            if response.status_code == 200:
                logger.info("Main app cache invalidated successfully")
            else:
                logger.warning(f"Main app reload returned {response.status_code}")
    except Exception as e:
        logger.warning(f"Could not notify main app: {e}")


async def _trigger_azure_extraction_job() -> dict:
    """Start a Container Apps Job for schema extraction (Azure production)."""
    try:
        from azure.identity import DefaultAzureCredential
        from azure.mgmt.appcontainers import ContainerAppsAPIClient

        credential = DefaultAzureCredential()
        subscription_id = os.environ["AZURE_SUBSCRIPTION_ID"]
        resource_group = os.environ["AZURE_RESOURCE_GROUP"]
        job_name = os.environ.get("SCHEMA_EXTRACTION_JOB_NAME", "job-nltodax-prod-schema")

        client = ContainerAppsAPIClient(credential, subscription_id)
        # Start the job
        client.jobs.begin_start(resource_group, job_name)

        return {
            "status": "started",
            "job_name": job_name,
            "message": "Schema extraction job triggered. Poll /api/schemas/extract/status for updates.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to trigger extraction job: {e}")


async def _run_local_extraction() -> dict:
    """Run schema extraction locally (development mode)."""
    import subprocess

    try:
        result = subprocess.run(
            [sys.executable, "schema_extraction/automated_schema_extract.py", "--save-json"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(_project_root),
        )

        if result.returncode == 0:
            return {
                "status": "completed",
                "message": "Local schema extraction completed",
                "output": result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout,
            }
        else:
            return {
                "status": "failed",
                "error": result.stderr[-1000:] if len(result.stderr) > 1000 else result.stderr,
            }
    except subprocess.TimeoutExpired:
        return {"status": "failed", "error": "Extraction timed out (120s)"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Frontend Static Files ──────────────────────────────────

frontend_dir = Path(__file__).parent / "frontend"
if frontend_dir.exists():
    app.mount("/assets", StaticFiles(directory=frontend_dir), name="admin-frontend")

    @app.get("/")
    async def serve_admin_ui():
        return FileResponse(frontend_dir / "index.html")


# ─── Entry Point ────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
