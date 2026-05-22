import os
import asyncio
import aiofiles
from datetime import datetime, timezone

import yaml
with open(os.environ.get("CONFIG_PATH", "config.yaml")) as f:
    CONFIG = yaml.safe_load(f)

LOGS_PATH = CONFIG["engine"]["logs_path"]


def _log_file_path(run_id: str) -> str:
    """Returns the path to the log file for a given run"""
    return os.path.join(LOGS_PATH, f"{run_id}.log")


async def write_log_line(run_id: str, job: str, line: str):
    """
    Appends a single log line to the run's log file on disk.
    Each line is timestamped at write time.
    Format: 2024-01-01T12:00:00Z [job_name] log line here
    """
    os.makedirs(LOGS_PATH, exist_ok=True)

    timestamp = datetime.now(timezone.utc).isoformat()
    formatted = f"{timestamp} [{job}] {line}\n"

    # aiofiles writes without blocking the event loop
    async with aiofiles.open(_log_file_path(run_id), mode="a") as f:
        await f.write(formatted)


async def stream_logs(run_id: str, follow: bool, is_active_fn=None):
    """
    Async generator that yields log lines for SSE streaming.
    
    - First yields all existing lines (backlog for late-connecting clients)
    - If follow=True, keeps watching the file for new lines
    - Never loads the whole file into memory — reads line by line
    
    This handles the 50MB log requirement cleanly.
    """
    log_path = _log_file_path(run_id)

    # Wait for log file to exist (build may not have started yet)
    for _ in range(20):
        if os.path.exists(log_path):
            break
        await asyncio.sleep(0.5)

    if not os.path.exists(log_path):
        return

    # Stream existing lines first (backlog)
    async with aiofiles.open(log_path, mode="r") as f:
        while True:
            line = await f.readline()
            if line:
                yield line.rstrip()
            else:
                # Reached end of current content
                if not follow:
                    break
                
                # If a run is no longer active (terminal state), we shouldn't wait for new lines forever.
                if is_active_fn and not is_active_fn():
                    # Check one last time for any straggler lines
                    line = await f.readline()
                    while line:
                        yield line.rstrip()
                        line = await f.readline()
                    break

                # follow=True: wait a moment and check for new lines
                await asyncio.sleep(0.2)