import uuid
import asyncio
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.responses import StreamingResponse
import aiofiles

from engine.parser import parse_pipeline
from engine.scheduler import build_dag, detect_cycles, get_parallel_groups
from engine.runner import execute_pipeline
from engine.logs import stream_logs
from registry.auth import validate_token

app = FastAPI()

# In-memory run store — stores status of each run
# In production this would be a DB, but for now a dict is fine
runs: dict = {}


def get_run_or_404(run_id: str) -> dict:
    if run_id not in runs:
        raise HTTPException(status_code=404, detail="Run not found")
    return runs[run_id]


@app.get("/health")
def health():
    return {"status": "ok", "service": "engine"}


@app.post("/runs", status_code=201)
async def create_run(pipeline: UploadFile = File(...), publisher: str = Depends(validate_token)):
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
    # This lets us return the run_id immediately
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
        async for line in stream_logs(run_id, follow):
            # SSE format requires "data: " prefix and double newline
            yield f"data: {line}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # important for nginx not to buffer
        }
    )