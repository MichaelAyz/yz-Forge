import os
import yaml

# Load config FIRST before anything else
with open(os.environ.get("CONFIG_PATH", "config.yaml")) as f:
    CONFIG = yaml.safe_load(f)

# Now import everything else
import asyncio
import hashlib
import httpx
import docker
import time
from datetime import datetime

from engine.logs import write_log_line
from engine.alerts import alert_pipeline_event, alert_integrity_failure, alert_resolution_failure


# Now read from config
REGISTRY_URL = CONFIG["engine"]["registry_url"]
LOGS_PATH = CONFIG["engine"]["logs_path"]
# Where the engine WRITES files (internal container path)
RUNS_PATH = CONFIG["engine"].get("runs_path", "/app/data/runs")
# Where Docker mounts FROM (host path)
HOST_DATA_PATH = os.environ.get("HOST_DATA_PATH", os.path.abspath("./data"))
DEFAULT_TIMEOUT = CONFIG["engine"]["default_job_timeout"]


# ─────────────────────────────────────────────
# MAIN PIPELINE EXECUTOR
# ─────────────────────────────────────────────

async def execute_pipeline(run_id: str, runs: dict):
    """
    Main entry point. Called in the background after POST /runs.
    Drives the entire pipeline from resolution to artifact publishing.
    """
    run = runs[run_id]
    pipeline = run["pipeline"]
    start_time = time.time()

    await _log(run_id, "system", f"Pipeline '{pipeline['name']}' started")
    run["status"] = "running"
    asyncio.create_task(alert_pipeline_event(pipeline["name"], run_id, "running"))

    # ── Step 1: Resolve dependencies ──────────────────────────────
    deps = pipeline.get("dependencies", [])
    if deps:
        await _log(run_id, "system", "Resolving dependencies...")
        try:
            lockfile = await resolve_dependencies(deps)
            run["lockfile"] = lockfile
            await _log(run_id, "system", "Dependencies resolved successfully")
        except ConflictError as e:
            await _log(run_id, "system", f"Dependency conflict: {e}")
            run["status"] = "conflict_failure"
            asyncio.create_task(alert_resolution_failure(pipeline["name"], f"Dependency conflict: {e}"))
            return
        except Exception as e:
            await _log(run_id, "system", f"Resolution failed: {e}")
            run["status"] = "failed"
            asyncio.create_task(alert_resolution_failure(pipeline["name"], f"Resolution failed: {e}"))
            return
    else:
        run["lockfile"] = {"packages": []}

    try:
        # ── Step 2: Download and verify dependencies ───────────────────
        # Engine writes to its internal path
        workspace = os.path.join(RUNS_PATH, run_id)
        deps_dir = os.path.join(workspace, "deps")
        os.makedirs(deps_dir, exist_ok=True)

        for pkg in run["lockfile"].get("packages", []):
            await _log(run_id, "system", f"Pulling {pkg['name']}@{pkg['version']}")
            try:
                await pull_and_verify(pkg, deps_dir, run_id)
            except IntegrityError as e:
                await _log(run_id, "system", f"INTEGRITY FAILURE: {e}")
                run["status"] = "integrity_failure"
                asyncio.create_task(alert_integrity_failure(run_id, pkg["name"], pkg["version"], e.expected, e.actual))
                return

        # ── Step 3: Execute job groups in order ────────────────────────
        # groups is a list of lists e.g. [["build"], ["test", "lint"], ["deploy"]]
        # Jobs in the same group run in parallel
        for group in run["groups"]:
            await _log(run_id, "system", f"Starting jobs: {group}")

            # Run all jobs in this group at the same time
            tasks = [
                run_job(run_id, job_name, pipeline["jobs"][job_name], workspace, runs)
                for job_name in group
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Check if any job in this group failed
            group_failed = False
            for job_name, result in zip(group, results):
                if isinstance(result, Exception) or run["jobs"][job_name]["status"] == "failed":
                    group_failed = True
                    await _log(run_id, "system", f"Job '{job_name}' failed")

            # If any job failed, mark remaining jobs as skipped
            if group_failed:
                remaining_jobs = _get_remaining_jobs(run, group)
                for job_name in remaining_jobs:
                    run["jobs"][job_name]["status"] = "skipped"
                    await _log(run_id, "system", f"Job '{job_name}' skipped due to upstream failure")
                run["status"] = "failed"
                duration = time.time() - start_time
                failing_job = next((j for j, info in run["jobs"].items() if info["status"] == "failed"), None)
                asyncio.create_task(alert_pipeline_event(pipeline["name"], run_id, "failed", duration_seconds=duration, failing_job=failing_job))
                return

        # ── Step 4: Publish artifacts ──────────────────────────────────
        artifacts = pipeline.get("artifacts", [])
        for artifact in artifacts:
            artifact_path = os.path.join(workspace, artifact["path"].lstrip("./"))
            if os.path.exists(artifact_path):
                await _log(run_id, "system", f"Publishing {artifact['name']}@{artifact['version']}")
                await publish_artifact(artifact, artifact_path, run_id)
            else:
                await _log(run_id, "system", f"Artifact path not found: {artifact_path}")

        run["status"] = "succeeded"
        await _log(run_id, "system", f"Pipeline '{pipeline['name']}' succeeded")
        duration = time.time() - start_time
        asyncio.create_task(alert_pipeline_event(pipeline["name"], run_id, "succeeded", duration_seconds=duration))

    except Exception as e:
        await _log(run_id, "system", f"Pipeline run encountered unexpected error: {e}")
        run["status"] = "failed"
        duration = time.time() - start_time
        asyncio.create_task(alert_pipeline_event(pipeline["name"], run_id, "failed", duration_seconds=duration, failing_job=str(e)))



# ─────────────────────────────────────────────
# SINGLE JOB RUNNER
# ─────────────────────────────────────────────

async def run_job(run_id: str, job_name: str, job: dict, workspace: str, runs: dict):
    run = runs[run_id]
    run["jobs"][job_name]["status"] = "running"

    await _log(run_id, job_name, f"Starting job '{job_name}' on {job['runtime']}")

    resources = job.get("resources", {})
    cpu_limit = float(resources.get("cpu", 1.0))
    mem_limit = resources.get("memory", "512m")

    if isinstance(mem_limit, str) and mem_limit.endswith("Mi"):
        mem_limit = mem_limit[:-2] + "m"

    # Build the shell script
    steps_script = "set -e\n"
    for step in job.get("steps", []):
        steps_script += f"echo '>>> Step: {step['name']}'\n"
        steps_script += step["run"] + "\n"

    # Write script to job workspace (internal engine path)
    job_workspace = os.path.join(workspace, job_name)
    os.makedirs(job_workspace, exist_ok=True)

    script_path = os.path.join(job_workspace, "_forge_run.sh")
    with open(script_path, "w") as f:
        f.write(steps_script)

    # Verify the script was written before starting the container
    if not os.path.exists(script_path):
        raise RuntimeError(f"Failed to write script to {script_path}")

    await _log(run_id, job_name, f"Script written to {script_path}")

    # Convert internal paths to host paths for Docker volume mounts.
    # The engine container has ./data mounted at /app/data, so
    # /app/data/runs/... (internal) maps to HOST_DATA_PATH/runs/... (host).
    abs_job_workspace = os.path.join(HOST_DATA_PATH, "runs", run_id, job_name)
    abs_deps_dir = os.path.join(HOST_DATA_PATH, "runs", run_id, "deps")

    await _log(run_id, job_name, f"Mounting workspace: {abs_job_workspace}")

    network_name = f"forge-{run_id[:8]}-{job_name}"
    client = docker.from_env()

    try:
        network = client.networks.create(
            network_name,
            driver="bridge",
            internal=True,
        )
    except Exception as e:
        await _log(run_id, job_name, f"Failed to create network: {e}")
        run["jobs"][job_name]["status"] = "failed"
        raise

    container = None
    try:
        container = client.containers.run(
            image=job["runtime"],
            command="sh /workspace/_forge_run.sh",
            detach=True,
            volumes={
                abs_job_workspace: {"bind": "/workspace", "mode": "rw"},
                abs_deps_dir:      {"bind": "/workspace/deps", "mode": "ro"},
            },
            nano_cpus=int(cpu_limit * 1e9),
            mem_limit=mem_limit,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
            environment={
                "FORGE_URL":   REGISTRY_URL,
                "FORGE_TOKEN": _get_build_token(),
            },
            network=network_name,
            remove=False,
        )

        await _log(run_id, job_name, f"Container {container.short_id} started")

        timeout_task = asyncio.create_task(
            _timeout_container(container, DEFAULT_TIMEOUT, run_id, job_name)
        )

        await _stream_container_logs(container, run_id, job_name)
        timeout_task.cancel()

        container.reload()
        exit_code = container.attrs["State"]["ExitCode"]

        if exit_code == 0:
            run["jobs"][job_name]["status"] = "succeeded"
            await _log(run_id, job_name, f"Job '{job_name}' succeeded")
        else:
            run["jobs"][job_name]["status"] = "failed"
            await _log(run_id, job_name, f"Job '{job_name}' failed with exit code {exit_code}")
            raise RuntimeError(f"Job failed with exit code {exit_code}")

    except Exception as e:
        run["jobs"][job_name]["status"] = "failed"
        await _log(run_id, job_name, f"Job error: {e}")
        raise

    finally:
        if container:
            try:
                container.remove(force=True)
            except Exception:
                pass
        try:
            network.remove()
        except Exception:
            pass


# ─────────────────────────────────────────────
# LOG STREAMING FROM CONTAINER
# ─────────────────────────────────────────────

async def _stream_container_logs(container, run_id: str, job_name: str):
    """
    Reads stdout/stderr from the container line by line.
    Writes each line to our log system as it arrives.
    Does NOT buffer everything in memory.
    """
    # Run the blocking Docker log stream in a thread
    # so it doesn't block our async event loop
    loop = asyncio.get_event_loop()

    def _read_logs():
        for chunk in container.logs(stream=True, follow=True):
            line = chunk.decode("utf-8", errors="replace").rstrip()
            if line:
                # Schedule log write on the event loop from this thread
                asyncio.run_coroutine_threadsafe(
                    write_log_line(run_id, job_name, line),
                    loop
                )

    await loop.run_in_executor(None, _read_logs)


# ─────────────────────────────────────────────
# TIMEOUT ENFORCEMENT
# ─────────────────────────────────────────────

async def _timeout_container(container, timeout_seconds: int, run_id: str, job_name: str):
    """
    Kills the container if it runs longer than timeout_seconds.
    Runs as a parallel task alongside log streaming.
    """
    await asyncio.sleep(timeout_seconds)
    await _log(run_id, job_name, f"TIMEOUT: Job exceeded {timeout_seconds}s limit. Killing container.")
    try:
        container.kill()
    except Exception:
        pass


# ─────────────────────────────────────────────
# DEPENDENCY RESOLUTION + PULL
# ─────────────────────────────────────────────

async def resolve_dependencies(deps: list) -> dict:
    """
    Calls the registry's resolver endpoint with the pipeline's
    dependency list. Returns a lockfile dict.
    """
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{REGISTRY_URL}/resolve",
            json={"dependencies": deps},
            timeout=30.0
        )
        if response.status_code == 409:
            raise ConflictError(response.json().get("detail", "Version conflict"))
        if response.status_code != 200:
            raise RuntimeError(f"Resolution failed: {response.text}")
        return response.json()


async def pull_and_verify(pkg: dict, deps_dir: str, run_id: str):
    """
    Downloads a dependency from the registry.
    Recomputes its SHA-256 from the downloaded bytes.
    Compares against the lockfile hash.
    Fails loudly if they don't match.
    """
    name         = pkg["name"]
    version      = pkg["version"]
    expected_sha = pkg["sha256"]

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{REGISTRY_URL}/artifacts/{name}/{version}",
            timeout=60.0
        )
        if response.status_code != 200:
            raise RuntimeError(f"Could not pull {name}@{version}: {response.status_code}")

    # Compute SHA-256 of received bytes and compare against lockfile
    actual_sha = hashlib.sha256(response.content).hexdigest()
    if actual_sha != expected_sha:
        raise IntegrityError(
            f"{name}@{version} — "
            f"expected sha256:{expected_sha} "
            f"but got sha256:{actual_sha}",
            expected=expected_sha,
            actual=actual_sha
        )

    # Save to deps directory
    pkg_dir = os.path.join(deps_dir, name)
    os.makedirs(pkg_dir, exist_ok=True)
    with open(os.path.join(pkg_dir, f"{name}-{version}.tar.gz"), "wb") as f:
        f.write(response.content)


# ─────────────────────────────────────────────
# ARTIFACT PUBLISHING
# ─────────────────────────────────────────────

async def publish_artifact(artifact: dict, file_path: str, run_id: str):
    """
    Publishes a built artifact to the registry after a successful job.
    Computes SHA-256 of the file before sending.
    """
    with open(file_path, "rb") as f:
        file_bytes = f.read()

    sha256 = hashlib.sha256(file_bytes).hexdigest()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{REGISTRY_URL}/artifacts/{artifact['name']}/{artifact['version']}",
            files={"file": (os.path.basename(file_path), file_bytes)},
            data={"checksum": f"sha256:{sha256}"},
            headers={"Authorization": f"Bearer {_get_build_token()}"},
            timeout=60.0
        )
        if response.status_code == 409:
            raise RuntimeError(
                f"Artifact {artifact['name']}@{artifact['version']} already exists"
            )
        if response.status_code not in (200, 201):
            raise RuntimeError(f"Publish failed: {response.text}")


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

async def _log(run_id: str, job: str, line: str):
    """Shorthand for writing a log line."""
    await write_log_line(run_id, job, line)


def _get_build_token() -> str:
    """
    Returns the internal build token the engine uses
    to talk to the registry. Read from environment or config.
    """
    return os.environ.get("FORGE_INTERNAL_TOKEN", "internal-token")


def _get_remaining_jobs(run: dict, completed_group: list) -> list:
    """
    Returns all jobs that haven't started yet
    (i.e. not in any completed group so far).
    """
    completed = set(completed_group)
    return [
        job for job, info in run["jobs"].items()
        if info["status"] == "queued" and job not in completed
    ]


# ─────────────────────────────────────────────
# CUSTOM EXCEPTIONS
# ─────────────────────────────────────────────

class IntegrityError(Exception):
    """Raised when a downloaded artifact's checksum doesn't match."""
    def __init__(self, message, expected=None, actual=None):
        super().__init__(message)
        self.expected = expected
        self.actual = actual


class ConflictError(Exception):
    """Raised when dependency resolution finds a version conflict."""
    pass