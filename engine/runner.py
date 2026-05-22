# TODO Person 2
# run_job(job, workspace, registry_url, token) -> status
# spins up a Docker container with:
#   - isolated network (only registry reachable)
#   - CPU/memory limits from YAML
#   - own workspace mounted
#   - streams stdout/stderr to logs.py

def run_job(job: dict, workspace: str, registry_url: str, token: str):
    raise NotImplementedError