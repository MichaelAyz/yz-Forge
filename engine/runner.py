import os
import yaml

# Load config FIRST before anything else
with open(os.environ.get("CONFIG_PATH", "config.yaml")) as f:
    CONFIG = yaml.safe_load(f)

import asyncio
import hashlib
import httpx
import docker
import time
from datetime import datetime

from engine.logs import write_log_line
from engine.alerts import alert_pipeline_event, alert_integrity_failure, alert_resolution_failure

REGISTRY_URL = CONFIG["engine"]["registry_url"]
LOGS_PATH = CONFIG["engine"]["logs_path"]
RUNS_PATH = CONFIG["engine"].get("runs_path", "/app/data/runs")
HOST_DATA_PATH = os.environ.get("HOST_DATA_PATH", os.path.abspath("./data"))
DEFAULT_TIMEOUT = CONFIG["engine"]["default_job_timeout"]

# For build containers to reach the registry, use host.docker.internal (works on Docker Desktop & Linux)
BUILD_REGISTRY_URL = "http://host.docker.internal:8002"


# ─────────────────────────────────────────────
# MAIN PIPELINE EXECUTOR
# ─────────────────────────────────────────────

async def execute_pipeline(run_id: str, runs: dict):
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
            asyncio.create_task(alert_resolution_failure(pipeline["name"], str(e)))
            return
        except Exception as e:
            await _log(run_id, "system", f"Resolution failed: {e}")
            # Check if it's a version conflict from the registry
            error_str = str(e)
            if "conflict" in error_str.lower() or "Version conflict" in error_str:
                run["status"] = "conflict_failure"
                asyncio.create_task(alert_resolution_failure(pipeline["name"], error_str))
            else:
                run["status"] = "failed"
            return
    else:
        run["lockfile"] = {"packages": {}}

    try:
        # ── Step 2: Download and verify dependencies ───────────────────
        workspace = os.path.join(RUNS_PATH, run_id)
        deps_dir = os.path.join(workspace, "deps")
        os.makedirs(deps_dir, exist_ok=True)

        for pkg_name, pkg_info in run["lockfile"].get("packages", {}).items():
            await _log(run_id, "system", f"Pulling {pkg_name}@{pkg_info['version']}")
            try:
                await pull_and_verify(pkg_name, pkg_info, deps_dir, run_id)
            except IntegrityError as e:
                await _log(run_id, "system", f"INTEGRITY FAILURE: {e}")
                run["status"] = "integrity_failure"
                asyncio.create_task(alert_integrity_failure(
                    run_id, pkg_name, pkg_info["version"], e.expected, e.actual
                ))
                return

        # ── Step 3: Execute job groups in order ────────────────────────
        for group in run["groups"]:
            await _log(run_id, "system", f"Starting jobs: {group}")

            tasks = [
                run_job(run_id, job_name, pipeline["jobs"][job_name], workspace, runs)
                for job_name in group
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            group_failed = False
            for job_name, result in zip(group, results):
                if isinstance(result, Exception) or run["jobs"][job_name]["status"] == "failed":
                    group_failed = True
                    await _log(run_id, "system", f"Job '{job_name}' failed")

            if group_failed:
                remaining_jobs = _get_remaining_jobs(run, group)
                for job_name in remaining_jobs:
                    run["jobs"][job_name]["status"] = "skipped"
                    await _log(run_id, "system", f"Job '{job_name}' skipped due to upstream failure")
                run["status"] = "failed"
                duration = time.time() - start_time
                failing_job = next((j for j, info in run["jobs"].items() if info["status"] == "failed"), None)
                asyncio.create_task(alert_pipeline_event(
                    pipeline["name"], run_id, "failed",
                    duration_seconds=duration, failing_job=failing_job
                ))
                return

        # ── Step 4: Publish artifacts ──────────────────────────────────
        artifacts = pipeline.get("artifacts", [])
        for artifact in artifacts:
            # Artifact path is relative to the producing job's workspace
            # The job runs in /workspace, so out.tar.gz is at <job_workspace>/out.tar.gz
            # We need to find which job produced it - for now, use the first job's workspace
            if run["groups"]:
                producing_job = run["groups"][-1][-1]  # Last job in last group
            else:
                producing_job = list(pipeline["jobs"].keys())[0]
            
            job_workspace = os.path.join(workspace, producing_job)
            artifact_path = os.path.join(job_workspace, artifact["path"].lstrip("./"))
            
            await _log(run_id, "system", f"Looking for artifact at: {artifact_path}")
            
            if os.path.exists(artifact_path):
                await _log(run_id, "system", f"Publishing {artifact['name']}@{artifact['version']}")
                try:
                    await publish_artifact(artifact, artifact_path, run_id)
                    await _log(run_id, "system", f"Published {artifact['name']}@{artifact['version']} successfully")
                except Exception as e:
                    await _log(run_id, "system", f"Failed to publish artifact: {e}")
            else:
                await _log(run_id, "system", f"Artifact path not found: {artifact_path}")

        run["status"] = "succeeded"
        await _log(run_id, "system", f"Pipeline '{pipeline['name']}' succeeded")
        duration = time.time() - start_time
        asyncio.create_task(alert_pipeline_event(
            pipeline["name"], run_id, "succeeded", duration_seconds=duration
        ))

    except Exception as e:
        await _log(run_id, "system", f"Pipeline run encountered unexpected error: {e}")
        run["status"] = "failed"
        duration = time.time() - start_time
        asyncio.create_task(alert_pipeline_event(
            pipeline["name"], run_id, "failed",
            duration_seconds=duration, failing_job=str(e)
        ))


# ─────────────────────────────────────────────
# SINGLE JOB RUNNER
# ─────────────────────────────────────────────

async def run_job(run_id: str, job_name: str, job: dict, workspace: str, runs: dict):
    run = runs[run_id]
    run["jobs"][job_name]["status"] = "running"

    await _log(run_id, job_name, f"Starting job '{job_name}' on {job['runtime']}")

    resources = job.get("resources", {})
    cpu_limit = float(resources.get("cpu", 1.0))
    mem_limit = resources.get("memory", "512Mi")

    # Normalize memory limit: convert "256Mi" to "256m" for Docker
    if isinstance(mem_limit, str):
        mem_limit = mem_limit.replace("Mi", "m").replace("Gi", "g")

    # Build the shell script
    steps_script = "set -e\n"
    for step in job.get("steps", []):
        steps_script += f"echo '>>> Step: {step['name']}'\n"
        steps_script += step["run"] + "\n"

    # Write script to job workspace
    job_workspace = os.path.join(workspace, job_name)
    os.makedirs(job_workspace, exist_ok=True)

    script_path = os.path.join(job_workspace, "_forge_run.sh")
    with open(script_path, "w") as f:
        f.write(steps_script)

    # Convert internal paths to host paths for Docker volume mounts
    abs_job_workspace = os.path.join(HOST_DATA_PATH, "runs", run_id, job_name)
    abs_deps_dir = os.path.join(HOST_DATA_PATH, "runs", run_id, "deps")

    await _log(run_id, job_name, f"Mounting workspace: {abs_job_workspace}")

    client = docker.from_env()

    container = None
    try:
        container = client.containers.run(
            image=job["runtime"],
            command="sh /workspace/_forge_run.sh", working_dir="/workspace",
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
                "FORGE_URL":   BUILD_REGISTRY_URL,
                "FORGE_TOKEN": _get_build_token(),
            },
            network_mode="none",  # Complete network isolation
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
            # Check if it was killed by OOM
            if container.attrs["State"].get("OOMKilled", False):
                await _log(run_id, job_name, "Job killed: Out of Memory (OOM)")
            run["jobs"][job_name]["status"] = "failed"
            await _log(run_id, job_name, f"Job '{job_name}' failed with exit code {exit_code}")
            raise RuntimeError(f"Job failed with exit code {exit_code}")

    except Exception as e:
        if run["jobs"][job_name]["status"] != "failed":
            run["jobs"][job_name]["status"] = "failed"
        await _log(run_id, job_name, f"Job error: {e}")
        raise

    finally:
        if container:
            try:
                container.remove(force=True)
            except Exception:
                pass


# ─────────────────────────────────────────────
# LOG STREAMING FROM CONTAINER
# ─────────────────────────────────────────────

async def _stream_container_logs(container, run_id: str, job_name: str):
    loop = asyncio.get_event_loop()

    def _read_logs():
        for chunk in container.logs(stream=True, follow=True):
            line = chunk.decode("utf-8", errors="replace").rstrip()
            if line:
                asyncio.run_coroutine_threadsafe(
                    write_log_line(run_id, job_name, line),
                    loop
                )

    await loop.run_in_executor(None, _read_logs)


# ─────────────────────────────────────────────
# TIMEOUT ENFORCEMENT
# ─────────────────────────────────────────────

async def _timeout_container(container, timeout_seconds: int, run_id: str, job_name: str):
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
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{REGISTRY_URL}/resolve",
            json={"dependencies": deps},
            timeout=30.0
        )
        if response.status_code == 400:
            detail = response.json().get("detail", {})
            if isinstance(detail, dict):
                error_type = detail.get("error", "")
                message = detail.get("message", str(detail))
                if "conflict" in error_type or "conflict" in message.lower():
                    raise ConflictError(message)
            raise RuntimeError(f"Resolution failed: {response.text}")
        if response.status_code != 200:
            raise RuntimeError(f"Resolution failed: {response.text}")
        return response.json()


async def pull_and_verify(pkg_name: str, pkg_info: dict, deps_dir: str, run_id: str):
    version = pkg_info["version"]
    expected_sha = pkg_info["sha256"]

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{REGISTRY_URL}/artifacts/{pkg_name}/{version}",
            timeout=60.0
        )
        if response.status_code != 200:
            raise RuntimeError(f"Could not pull {pkg_name}@{version}: {response.status_code}")

    actual_sha = hashlib.sha256(response.content).hexdigest()
    if actual_sha != expected_sha:
        raise IntegrityError(
            f"{pkg_name}@{version} — "
            f"expected sha256:{expected_sha} "
            f"but got sha256:{actual_sha}",
            expected=expected_sha,
            actual=actual_sha
        )

    pkg_dir = os.path.join(deps_dir, pkg_name)
    os.makedirs(pkg_dir, exist_ok=True)
    file_path = os.path.join(pkg_dir, f"{pkg_name}-{version}.tar.gz")
    with open(file_path, "wb") as f:
        f.write(response.content)

    await _log(run_id, "system", f"Downloaded and verified {pkg_name}@{version}")


# ─────────────────────────────────────────────
# ARTIFACT PUBLISHING
# ─────────────────────────────────────────────

async def publish_artifact(artifact: dict, file_path: str, run_id: str):
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
    await write_log_line(run_id, job, line)


def _get_build_token() -> str:
    return os.environ.get("FORGE_INTERNAL_TOKEN", "internal-token")


def _get_remaining_jobs(run: dict, completed_group: list) -> list:
    completed = set(completed_group)
    return [
        job for job, info in run["jobs"].items()
        if info["status"] == "queued" and job not in completed
    ]


# ─────────────────────────────────────────────
# CUSTOM EXCEPTIONS
# ─────────────────────────────────────────────

class IntegrityError(Exception):
    def __init__(self, message, expected=None, actual=None):
        super().__init__(message)
        self.expected = expected
        self.actual = actual


class ConflictError(Exception):
    pass
