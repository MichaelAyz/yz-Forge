import os
import yaml
import json
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import FileResponse

from registry.auth import init_auth, require_auth
from registry.metadata import init_db, save_artifact, get_artifact, list_versions, ConflictError
from registry.storage import init_storage, save_blob, get_blob, verify_checksum
from registry.resolver import resolve, Version

app = FastAPI(title="Forge Registry")

# Load configuration from config.yaml
config_path = os.environ.get("CONFIG_PATH", "config.yaml")
if os.path.exists(config_path):
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
else:
    config = {}

registry_config = config.get("registry", {})
storage_path = registry_config.get("storage_path", "./data/blobs")
db_path = registry_config.get("db_path", "./data/forge.db")

# Ensure directories exist
Path(db_path).parent.mkdir(parents=True, exist_ok=True)
Path(storage_path).mkdir(parents=True, exist_ok=True)

# Initialize modules
init_auth(db_path)
init_db(db_path)
init_storage(storage_path)


@app.get("/health")
def health():
    return {"status": "ok", "service": "registry"}


@app.post("/artifacts/{name}/{version}", status_code=201)
async def upload_artifact(
    name: str,
    version: str,
    file: UploadFile = File(...),
    checksum: str = Form(...),
    dependencies: Optional[str] = Form(None),
    dependencies_query: Optional[str] = Query(None, alias="dependencies"),
    publisher: str = Depends(require_auth)
):
    """Upload a new artifact.

    Validation rules:
    - Version must be valid SemVer (400 on error)
    - Checksum must match the computed SHA-256 of the uploaded file (400 on error)
    - Immutability: duplicate upload of (name, version) is rejected (409 on error)
    """
    # 1. Validate version is valid SemVer
    try:
        Version.parse(version)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid semver version: {str(e)}"
        )

    # 2. Read file content and compute hash
    file_bytes = await file.read()
    size = len(file_bytes)

    # Clean the checksum (strip 'sha256:' if present)
    expected_sha256 = checksum.strip()
    if expected_sha256.startswith("sha256:"):
        expected_sha256 = expected_sha256[7:]

    # 3. Verify checksum
    if not verify_checksum(file_bytes, expected_sha256):
        raise HTTPException(
            status_code=400,
            detail="Checksum mismatch. Upload rejected."
        )

    # 4. Parse optional dependencies
    deps_list = []
    deps_str = dependencies or dependencies_query
    if deps_str:
        try:
            deps_list = json.loads(deps_str)
            if not isinstance(deps_list, list):
                raise ValueError("Dependencies must be a JSON array.")
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid dependencies format: {str(e)}"
            )

    # 5. Save metadata (returns 409 if already exists)
    try:
        save_artifact(
            name=name,
            version=version,
            sha256=expected_sha256,
            size=size,
            deps=deps_list,
            publisher=publisher
        )
    except ConflictError as e:
        raise HTTPException(
            status_code=409,
            detail=str(e)
        )

    # 6. Save the blob content to disk
    save_blob(file_bytes)

    return {
        "status": "published",
        "name": name,
        "version": version,
        "sha256": expected_sha256,
        "size": size
    }


@app.get("/artifacts/{name}/{version}")
def download_artifact(name: str, version: str):
    """Download artifact blob.

    Returns the blob and the X-Artifact-SHA256 header.
    """
    meta = get_artifact(name, version)
    if not meta:
        raise HTTPException(status_code=404, detail="Artifact not found")

    sha256_hex = meta["sha256"]
    try:
        path = get_blob(sha256_hex)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Artifact file not found in storage")

    return FileResponse(
        path,
        headers={"X-Artifact-SHA256": sha256_hex},
        media_type="application/octet-stream"
    )


@app.get("/artifacts/{name}/{version}/meta")
def get_artifact_meta(name: str, version: str):
    """Retrieve metadata of a specific artifact version."""
    meta = get_artifact(name, version)
    if not meta:
        raise HTTPException(status_code=404, detail="Artifact not found")

    try:
        deps = json.loads(meta["deps_json"])
    except Exception:
        deps = []

    return {
        "name": meta["name"],
        "version": meta["version"],
        "sha256": meta["sha256"],
        "size": meta["size"],
        "deps": deps,
        "published_at": meta["published_at"]
    }


@app.get("/artifacts/{name}")
def list_package_versions(name: str):
    """List all stored versions of a package name, sorted by SemVer."""
    versions = list_versions(name)
    
    # Sort versions using Version class
    parsed_versions = []
    for v in versions:
        try:
            parsed_versions.append((Version.parse(v), v))
        except ValueError:
            parsed_versions.append((Version(0, 0, 0), v))
            
    parsed_versions.sort(key=lambda x: x[0])
    sorted_versions = [v for _, v in parsed_versions]
    
    return {"versions": sorted_versions}


@app.post("/resolve")
def resolve_dependencies(req: dict):
    """Internal HTTP endpoint allowing the engine or CLI to resolve a dependency graph.

    Input format:
        {"dependencies": [{"name": "lib-core", "version": "^1.0.0"}]}
    """
    deps = req.get("dependencies", [])
    try:
        lockfile = resolve(deps)
        return lockfile
    except ValueError as e:
        error_msg = str(e)
        # Determine error type for structured response
        if "cycle" in error_msg.lower():
            raise HTTPException(
                status_code=400,
                detail={"error": "cycle_failure", "message": error_msg}
            )
        else:
            raise HTTPException(
                status_code=400,
                detail={"error": "conflict_failure", "message": error_msg}
            )