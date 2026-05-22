from collections import defaultdict, deque

def build_dag(jobs: dict) -> dict:
    """
    Takes the jobs dict from the parsed pipeline.
    Returns an adjacency list: { job_name: [list of jobs it depends on] }
    
    Example:
        jobs:
          build: {}
          test:
            needs: [build]
          deploy:
            needs: [test]
        
        Returns: { "build": [], "test": ["build"], "deploy": ["test"] }
    """
    dag = {}
    for job_name, job_body in jobs.items():
        needs = job_body.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]  # handle needs: build (not a list)
        dag[job_name] = needs

    # Make sure every job referenced in needs actually exists
    all_jobs = set(dag.keys())
    for job_name, deps in dag.items():
        for dep in deps:
            if dep not in all_jobs:
                raise ValueError(
                    f"Job '{job_name}' depends on '{dep}' which does not exist"
                )

    return dag


def detect_cycles(dag: dict):
    """
    Uses DFS to detect cycles in the job DAG.
    Raises ValueError naming the cycle if one is found.
    
    A cycle means job A needs B, B needs C, C needs A — impossible to run.
    """
    # Track visit state for each node
    # 0 = unvisited, 1 = currently visiting, 2 = done
    state = {job: 0 for job in dag}
    path = []  # tracks current DFS path for error message

    def dfs(job):
        state[job] = 1  # mark as currently visiting
        path.append(job)

        for dep in dag[job]:
            if state[dep] == 1:
                # We hit a node we're currently visiting = cycle found
                cycle_start = path.index(dep)
                cycle = path[cycle_start:] + [dep]
                raise ValueError(
                    f"Cycle detected in job dependencies: {' -> '.join(cycle)}"
                )
            if state[dep] == 0:
                dfs(dep)

        path.pop()
        state[job] = 2  # mark as done

    for job in dag:
        if state[job] == 0:
            dfs(job)


def topological_sort(dag: dict) -> list:
    """
    Returns jobs in the order they must run.
    Jobs that have no dependencies come first.
    
    Uses Kahn's algorithm (BFS-based).
    """
    # Count how many unresolved dependencies each job has
    in_degree = {job: 0 for job in dag}
    for job, deps in dag.items():
        for dep in deps:
            in_degree[job] += 1

    # Start with jobs that have no dependencies
    queue = deque([job for job, degree in in_degree.items() if degree == 0])
    order = []

    while queue:
        job = queue.popleft()
        order.append(job)

        # For every job that depends on this one, reduce their count
        for other_job, deps in dag.items():
            if job in deps:
                in_degree[other_job] -= 1
                if in_degree[other_job] == 0:
                    queue.append(other_job)

    if len(order) != len(dag):
        raise ValueError("Could not resolve job order — cycle may exist")

    return order


def get_parallel_groups(dag: dict) -> list:
    """
    Returns jobs grouped by which ones can run at the same time.
    
    Example:
        Group 1: [build-core, build-utils]   ← no dependencies, run together
        Group 2: [test]                       ← needs both above
        Group 3: [deploy]                     ← needs test
    
    This is what the runner uses to decide what to launch in parallel.
    """
    # Track which jobs are "done" so far
    completed = set()
    groups = []
    remaining = set(dag.keys())

    while remaining:
        # A job is ready if all its dependencies are already completed
        ready = [
            job for job in remaining
            if all(dep in completed for dep in dag[job])
        ]

        if not ready:
            raise ValueError(
                "Could not find any runnable jobs — possible cycle"
            )

        groups.append(ready)
        completed.update(ready)
        remaining -= set(ready)

    return groups