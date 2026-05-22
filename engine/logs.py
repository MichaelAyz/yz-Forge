# TODO Person 3
# write_log_line(run_id, job, line) -> None  (appends to disk)
# stream_logs(run_id, follow) -> async generator of SSE lines
# get_backlog(run_id) -> list of past log lines

def write_log_line(run_id: str, job: str, line: str):
    raise NotImplementedError

async def stream_logs(run_id: str, follow: bool):
    raise NotImplementedError