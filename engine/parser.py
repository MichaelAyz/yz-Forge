import yaml

# These are the only top-level fields allowed in a pipeline YAML.
# If someone adds an unknown field, we reject it with a clear error.
REQUIRED_TOP_LEVEL = {"name", "version", "jobs"}
ALLOWED_TOP_LEVEL  = {"name", "version", "dependencies", "jobs", "artifacts"}

# For each job, these fields are allowed
ALLOWED_JOB_FIELDS = {"runtime", "resources", "steps", "needs"}

def parse_pipeline(content: str) -> dict:
    """
    Takes raw YAML string, validates it, and returns a clean dict.
    Raises ValueError with a helpful message if anything is wrong.
    """

    # Load YAML — we use a Loader that tracks line numbers
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML: {e}")

    if not isinstance(data, dict):
        raise ValueError("Pipeline must be a YAML mapping at the top level")

    # Check for unknown top-level fields
    unknown = set(data.keys()) - ALLOWED_TOP_LEVEL
    if unknown:
        raise ValueError(f"Unknown fields in pipeline: {unknown}")

    # Check required fields exist
    missing = REQUIRED_TOP_LEVEL - set(data.keys())
    if missing:
        raise ValueError(f"Missing required fields: {missing}")

    # Validate name and version are strings
    if not isinstance(data["name"], str):
        raise ValueError("'name' must be a string")
    if not isinstance(data["version"], str):
        raise ValueError("'version' must be a string")

    # Validate jobs section
    jobs = data.get("jobs")
    if not isinstance(jobs, dict) or len(jobs) == 0:
        raise ValueError("'jobs' must be a non-empty mapping")

    for job_name, job_body in jobs.items():
        if not isinstance(job_body, dict):
            raise ValueError(f"Job '{job_name}' must be a mapping")

        # Check for unknown fields inside each job
        unknown_job_fields = set(job_body.keys()) - ALLOWED_JOB_FIELDS
        if unknown_job_fields:
            raise ValueError(
                f"Job '{job_name}' has unknown fields: {unknown_job_fields}"
            )

        # Every job must have a runtime and steps
        if "runtime" not in job_body:
            raise ValueError(f"Job '{job_name}' is missing 'runtime'")
        if "steps" not in job_body:
            raise ValueError(f"Job '{job_name}' is missing 'steps'")

        # Validate steps is a list
        if not isinstance(job_body["steps"], list):
            raise ValueError(f"Job '{job_name}' steps must be a list")

        # Each step must have name and run
        for i, step in enumerate(job_body["steps"]):
            if "name" not in step:
                raise ValueError(
                    f"Job '{job_name}' step {i+1} is missing 'name'"
                )
            if "run" not in step:
                raise ValueError(
                    f"Job '{job_name}' step {i+1} is missing 'run'"
                )

    # Validate dependencies if present
    deps = data.get("dependencies", [])
    if not isinstance(deps, list):
        raise ValueError("'dependencies' must be a list")

    for dep in deps:
        if "name" not in dep or "version" not in dep:
            raise ValueError(
                f"Each dependency must have 'name' and 'version'. Got: {dep}"
            )

    # Validate artifacts if present
    artifacts = data.get("artifacts", [])
    if not isinstance(artifacts, list):
        raise ValueError("'artifacts' must be a list")

    for art in artifacts:
        if "name" not in art or "version" not in art or "path" not in art:
            raise ValueError(
                f"Each artifact must have 'name', 'version', and 'path'. Got: {art}"
            )

    return data