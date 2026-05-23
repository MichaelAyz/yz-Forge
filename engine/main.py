import os
import yaml
import uuid
import asyncio
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.responses import StreamingResponse
import aiofiles

from engine.parser import parse_pipeline
from engine.scheduler import build_dag, detect_cycles, get_parallel_groups
from engine.runner import execute_pipeline
from engine.logs import stream_logs
from registry.auth import init_auth, require_auth

# Load config
config_path = os.environ.get("CONFIG_PATH", "config.yaml")
if os.path.exists(config_path):
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
else:
    config = {}

# Initialize auth with the same database as the registry
db_path = config.get("registry", {}).get("db_path", "./data/forge.db")
os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
init_auth(db_path)

def _seed_internal_token():
    """
    Auto-seeds the internal engine token on every startup.
    This ensures the engine can always publish artifacts to the registry
    even after a fresh docker compose up wipes the database.
    """
    import hashlib
    import sqlite3
    from datetime import datetime, timezone

    internal_token = os.environ.get("FORGE_INTERNAL_TOKEN", "internal-token")
    token_hash = hashlib.sha256(internal_token.encode()).hexdigest()
    created_at = datetime.now(timezone.utc).isoformat()

    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO tokens (label, token_hash, created_at) VALUES (?, ?, ?)",
                ("internal-engine", token_hash, created_at),
            )
            conn.commit()
    except Exception as e:
        print(f"Warning: could not seed internal token: {e}")

_seed_internal_token()

app = FastAPI()

# In-memory run store
runs: dict = {}


def get_run_or_404(run_id: str) -> dict:
    if run_id not in runs:
        raise HTTPException(status_code=404, detail="Run not found")
    return runs[run_id]


@app.get("/health")
def health():
    return {"status": "ok", "service": "engine"}


@app.post("/runs", status_code=201)
async def create_run(pipeline: UploadFile = File(...), publisher: str = Depends(require_auth)):
    """
    Accepts a pipeline YAML file.
    Validates it, builds the DAG, checks for cycles,
    then kicks off the build in the background.
    """
    content = await pipeline.read()

    # Parse and validate the YAML
    try:
        pipeline_data = parse_pipeline(content.decode("utf-8"))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Build DAG and check for cycles before starting anything
    try:
        dag = build_dag(pipeline_data["jobs"])
        detect_cycles(dag)
        groups = get_parallel_groups(dag)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Create a run record
    run_id = str(uuid.uuid4())
    runs[run_id] = {
        "run_id": run_id,
        "status": "queued",
        "pipeline": pipeline_data,
        "dag": dag,
        "groups": groups,
        "jobs": {
            job: {"status": "queued"} 
            for job in pipeline_data["jobs"]
        },
        "lockfile": None,
    }

    # Start the pipeline in the background
    asyncio.create_task(execute_pipeline(run_id, runs))

    return {"run_id": run_id}


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    """Returns current status of a run and all its jobs"""
    run = get_run_or_404(run_id)
    return {
        "run_id": run_id,
        "status": run["status"],
        "jobs": run["jobs"],
        "lockfile_url": f"/runs/{run_id}/lockfile" if run["lockfile"] else None,
    }


@app.get("/runs/{run_id}/lockfile")
def get_lockfile(run_id: str):
    """Returns the resolved lockfile for a run"""
    run = get_run_or_404(run_id)
    if not run["lockfile"]:
        raise HTTPException(status_code=404, detail="Lockfile not yet available")
    return run["lockfile"]


@app.get("/runs/{run_id}/logs")
async def get_logs(run_id: str, follow: bool = False):
    """
    Streams logs over SSE (Server-Sent Events).
    If follow=true, keeps the connection open and streams new lines live.
    """
    get_run_or_404(run_id)

    async def event_generator():
        def is_active():
            return runs.get(run_id, {}).get("status") in ["queued", "running"]

        async for line in stream_logs(run_id, follow, is_active_fn=is_active):
            yield f"data: {line}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )
