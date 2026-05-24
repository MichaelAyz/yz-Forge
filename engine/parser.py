import yaml

# These are the only top-level fields allowed in a pipeline YAML.
# If someone adds an unknown field, we reject it with a clear error.
REQUIRED_TOP_LEVEL = {"name", "version", "jobs"}
ALLOWED_TOP_LEVEL  = {"name", "version", "dependencies", "jobs", "artifacts", "__line__"}

# For each job, these fields are allowed
ALLOWED_JOB_FIELDS = {"runtime", "resources", "steps", "needs", "__line__"}

class LineTrackingLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        mapping = super().construct_mapping(node, deep=deep)
        mapping['__line__'] = node.start_mark.line + 1
        return mapping

LineTrackingLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    LineTrackingLoader.construct_mapping
)

def _strip_lines(obj):
    if isinstance(obj, dict):
        obj.pop('__line__', None)
        for v in obj.values():
            _strip_lines(v)
    elif isinstance(obj, list):
        for item in obj:
            _strip_lines(item)
    return obj

def parse_pipeline(content: str) -> dict:
    """
    Takes raw YAML string, validates it, and returns a clean dict.
    Raises ValueError with a helpful message if anything is wrong.
    """
    try:
        data = yaml.load(content, Loader=LineTrackingLoader)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML: {e}")

    if not data:
        raise ValueError("Pipeline is empty")

    if not isinstance(data, dict):
        raise ValueError("Pipeline must be a YAML mapping at the top level")

    root_line = data.get("__line__", 1)

    # Check for unknown top-level fields
    unknown = set(data.keys()) - ALLOWED_TOP_LEVEL
    if unknown:
        raise ValueError(f"Line {root_line}: Unknown fields in pipeline: {unknown}")

    # Check required fields exist
    missing = REQUIRED_TOP_LEVEL - set(data.keys())
    if missing:
        raise ValueError(f"Line {root_line}: Missing required fields: {missing}")

    # Validate name and version are strings
    if not isinstance(data["name"], str):
        raise ValueError(f"Line {root_line}: 'name' must be a string")
    if not isinstance(data["version"], str):
        raise ValueError(f"Line {root_line}: 'version' must be a string")

    # Validate jobs section
    jobs = data.get("jobs")
    if not isinstance(jobs, dict) or len(jobs) <= 1: # <= 1 because __line__ might be the only key if it's otherwise empty
        # If it's a dict but only has __line__, it's effectively empty of jobs
        if not isinstance(jobs, dict) or set(jobs.keys()) == {"__line__"}:
            jobs_line = jobs.get("__line__", root_line) if isinstance(jobs, dict) else root_line
            raise ValueError(f"Line {jobs_line}: 'jobs' must be a non-empty mapping")

    for job_name, job_body in jobs.items():
        if job_name == "__line__":
            continue

        if not isinstance(job_body, dict):
            raise ValueError(f"Line {jobs.get('__line__', root_line)}: Job '{job_name}' must be a mapping")

        job_line = job_body.get("__line__", root_line)

        # Check for unknown fields inside each job
        unknown_job_fields = set(job_body.keys()) - ALLOWED_JOB_FIELDS
        if unknown_job_fields:
            raise ValueError(
                f"Line {job_line}: Job '{job_name}' has unknown fields: {unknown_job_fields}"
            )

        # Every job must have a runtime and steps
        if "runtime" not in job_body:
            raise ValueError(f"Line {job_line}: Job '{job_name}' is missing 'runtime'")
        if "steps" not in job_body:
            raise ValueError(f"Line {job_line}: Job '{job_name}' is missing 'steps'")

        # Validate steps is a list
        if not isinstance(job_body["steps"], list):
            raise ValueError(f"Line {job_line}: Job '{job_name}' steps must be a list")

        # Each step must have name and run
        for i, step in enumerate(job_body["steps"]):
            step_line = step.get("__line__", job_line) if isinstance(step, dict) else job_line
            if not isinstance(step, dict):
                raise ValueError(f"Line {step_line}: Job '{job_name}' step {i+1} must be a mapping")
            if "name" not in step:
                raise ValueError(
                    f"Line {step_line}: Job '{job_name}' step {i+1} is missing 'name'"
                )
            if "run" not in step:
                raise ValueError(
                    f"Line {step_line}: Job '{job_name}' step {i+1} is missing 'run'"
                )

    # Validate dependencies if present
    deps = data.get("dependencies", [])
    if deps:
        if not isinstance(deps, list):
            raise ValueError(f"Line {root_line}: 'dependencies' must be a list")

        for dep in deps:
            dep_line = dep.get("__line__", root_line) if isinstance(dep, dict) else root_line
            if not isinstance(dep, dict) or "name" not in dep or "version" not in dep:
                raise ValueError(
                    f"Line {dep_line}: Each dependency must have 'name' and 'version'. Got: {dep}"
                )

    # Validate artifacts if present
    artifacts = data.get("artifacts", [])
    if artifacts:
        if not isinstance(artifacts, list):
            raise ValueError(f"Line {root_line}: 'artifacts' must be a list")

        for art in artifacts:
            art_line = art.get("__line__", root_line) if isinstance(art, dict) else root_line
            if not isinstance(art, dict) or "name" not in art or "version" not in art or "path" not in art:
                raise ValueError(
                    f"Line {art_line}: Each artifact must have 'name', 'version', and 'path'. Got: {art}"
                )

    return _strip_lines(data)