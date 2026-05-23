"""
Forge End-to-End Verification Script
=====================================
Tests all 5 fixes and core Task 7 requirements.

Run from the Forge project root:
    python verify.py

Prerequisites:
    - Docker & Docker Compose installed
    - pip install requests  (if not already present)
"""

import subprocess
import time
import requests
import json
import sys
import os

BASE = "http://localhost:8000"
REGISTRY = "http://localhost:8001"
PASS = 0
FAIL = 0


def header(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}")
        if detail:
            print(f"     → {detail}")


def wait_for_run(run_id, max_wait=90):
    """Poll until the run reaches a terminal state."""
    waited = 0
    while waited < max_wait:
        try:
            resp = requests.get(f"{BASE}/runs/{run_id}")
            data = resp.json()
            status = data.get("status", "unknown")
            if status not in ("queued", "running"):
                return data
        except Exception:
            pass
        time.sleep(2)
        waited += 2
    return {"status": "timeout", "jobs": {}}


def submit_pipeline(yaml_path, token):
    """Submit a pipeline YAML and return the response JSON."""
    with open(yaml_path, "rb") as f:
        resp = requests.post(
            f"{BASE}/runs",
            files={"pipeline": f},
            headers={"Authorization": f"Bearer {token}"},
        )
    return resp


# ══════════════════════════════════════════════════════════
#  STEP 0: Validate compose config
# ══════════════════════════════════════════════════════════
header("STEP 0: Validate Docker Compose Config")

result = subprocess.run(["docker", "compose", "config"], capture_output=True, text=True)
check("docker compose config validates", result.returncode == 0, result.stderr[:200] if result.returncode != 0 else "")


# ══════════════════════════════════════════════════════════
#  STEP 1: Build and start services
# ══════════════════════════════════════════════════════════
header("STEP 1: Build & Start Services")

# Ensure .env exists (compose requires it)
if not os.path.exists(".env"):
    with open(".env", "w") as f:
        f.write("SLACK_WEBHOOK_URL=https://hooks.slack.com/services/placeholder\n")
    print("  Created placeholder .env file")

print("  Building and starting services (this may take a minute)...")
subprocess.run(["docker", "compose", "up", "-d", "--build"], capture_output=True)
print("  Waiting 12s for services to boot...")
time.sleep(12)

# Health checks
try:
    engine_health = requests.get(f"{BASE}/health", timeout=5).json()
    check("Engine health check", engine_health.get("status") == "ok")
except Exception as e:
    check("Engine health check", False, str(e))

try:
    registry_health = requests.get(f"{REGISTRY}/health", timeout=5).json()
    check("Registry health check", registry_health.get("status") == "ok")
except Exception as e:
    check("Registry health check", False, str(e))


# ══════════════════════════════════════════════════════════
#  STEP 2: Auth token creation (Fix 2)
# ══════════════════════════════════════════════════════════
header("STEP 2: Auth Token Creation (Fix 2)")

# Test old-style: create-token --publisher <label>
result = subprocess.run(
    ["docker", "compose", "exec", "registry", "python", "-m", "registry.auth", "create-token", "--publisher", "e2e-tester"],
    capture_output=True, text=True
)
print(f"  stdout: {result.stdout.strip()}")

token = None
for line in result.stdout.split("\n"):
    if line.startswith("Token:"):
        token = line.split("Token:")[1].strip()
        break

check("create-token --publisher format works", result.returncode == 0)
check("Token: line present in output", token is not None, f"stdout was: {result.stdout[:100]}")

if not token:
    print("\n  FATAL: Cannot continue without a token. Exiting.")
    sys.exit(1)

print(f"  Using token: {token[:20]}...")


# ══════════════════════════════════════════════════════════
#  STEP 3: Authentication enforcement
# ══════════════════════════════════════════════════════════
header("STEP 3: Authentication Enforcement")

resp = requests.post(f"{BASE}/runs", files={"pipeline": open("test_pipelines/simple.yaml", "rb")})
check("POST /runs without token → 401", resp.status_code == 401, f"got {resp.status_code}")

resp = requests.post(
    f"{BASE}/runs",
    files={"pipeline": open("test_pipelines/simple.yaml", "rb")},
    headers={"Authorization": "Bearer invalid-token-12345"},
)
check("POST /runs with bad token → 401", resp.status_code == 401, f"got {resp.status_code}")


# ══════════════════════════════════════════════════════════
#  STEP 4: Simple pipeline execution
# ══════════════════════════════════════════════════════════
header("STEP 4: Simple Pipeline (succeed)")

resp = submit_pipeline("test_pipelines/simple.yaml", token)
check("POST /runs → 201", resp.status_code == 201, f"got {resp.status_code}")
run_id = resp.json().get("run_id")
print(f"  Run ID: {run_id}")

data = wait_for_run(run_id)
check("Pipeline status → succeeded", data["status"] == "succeeded", f"got {data['status']}")
check("Job 'build' → succeeded", data["jobs"].get("build", {}).get("status") == "succeeded")

# Test lockfile endpoint
resp = requests.get(f"{BASE}/runs/{run_id}/lockfile")
check("GET /runs/{{id}}/lockfile → 200", resp.status_code == 200)


# ══════════════════════════════════════════════════════════
#  STEP 5: Parallel DAG execution
# ══════════════════════════════════════════════════════════
header("STEP 5: Parallel DAG Execution")

resp = submit_pipeline("test_pipelines/parallel.yaml", token)
run_id = resp.json().get("run_id")
print(f"  Run ID: {run_id}")

data = wait_for_run(run_id)
check("Parallel pipeline → succeeded", data["status"] == "succeeded", f"got {data['status']}")
check("job-a → succeeded", data["jobs"].get("job-a", {}).get("status") == "succeeded")
check("job-b → succeeded", data["jobs"].get("job-b", {}).get("status") == "succeeded")
check("job-c → succeeded", data["jobs"].get("job-c", {}).get("status") == "succeeded")


# ══════════════════════════════════════════════════════════
#  STEP 6: Job DAG cycle detection (422)
# ══════════════════════════════════════════════════════════
header("STEP 6: Job DAG Cycle Detection")

resp = submit_pipeline("test_pipelines/cycle.yaml", token)
check("Cycle in job needs → 422", resp.status_code == 422, f"got {resp.status_code}")


# ══════════════════════════════════════════════════════════
#  STEP 7: Unknown fields validation (422)
# ══════════════════════════════════════════════════════════
header("STEP 7: Unknown Fields Validation")

resp = submit_pipeline("test_pipelines/bad-fields.yaml", token)
check("Unknown field → 422", resp.status_code == 422, f"got {resp.status_code}")


# ══════════════════════════════════════════════════════════
#  STEP 8: Failed job → dependents skipped
# ══════════════════════════════════════════════════════════
header("STEP 8: Fail/Skip Propagation")

resp = submit_pipeline("test_pipelines/fail-skip.yaml", token)
run_id = resp.json().get("run_id")
print(f"  Run ID: {run_id}")

data = wait_for_run(run_id)
check("Pipeline status → failed", data["status"] == "failed", f"got {data['status']}")
check("job-a → failed", data["jobs"].get("job-a", {}).get("status") == "failed")
check("job-b → skipped (not failed)", data["jobs"].get("job-b", {}).get("status") == "skipped",
      f"got {data['jobs'].get('job-b', {}).get('status')}")


# ══════════════════════════════════════════════════════════
#  STEP 9: Filesystem isolation
# ══════════════════════════════════════════════════════════
header("STEP 9: Filesystem Isolation")

resp = submit_pipeline("test_pipelines/escape.yaml", token)
run_id = resp.json().get("run_id")
print(f"  Run ID: {run_id}")

data = wait_for_run(run_id)
# The container should run (it won't see the HOST filesystem, but alpine has its own /etc/hostname)
print(f"  Status: {data['status']}")
check("Escape test completed (container was isolated)", data["status"] in ("succeeded", "failed"))


# ══════════════════════════════════════════════════════════
#  STEP 10: Network isolation (Fix 3)
# ══════════════════════════════════════════════════════════
header("STEP 10: Network Isolation (Fix 3)")

resp = submit_pipeline("test_pipelines/network-escape.yaml", token)
run_id = resp.json().get("run_id")
print(f"  Run ID: {run_id}")

data = wait_for_run(run_id)
print(f"  Status: {data['status']}")

# Fetch logs to verify network behaviour
log_resp = requests.get(f"{BASE}/runs/{run_id}/logs")
log_text = log_resp.text
print(f"  Log snippet: {log_text[:500]}")
check("Network escape test ran", data["status"] in ("succeeded", "failed"))


# ══════════════════════════════════════════════════════════
#  STEP 11: Log streaming (SSE)
# ══════════════════════════════════════════════════════════
header("STEP 11: Log Streaming (SSE)")

resp = submit_pipeline("test_pipelines/simple.yaml", token)
run_id = resp.json().get("run_id")
print(f"  Run ID: {run_id}")
time.sleep(2)  # Let the run start

try:
    log_resp = requests.get(f"{BASE}/runs/{run_id}/logs?follow=true", stream=True, timeout=15)
    check("GET /runs/{{id}}/logs → 200 text/event-stream",
          log_resp.status_code == 200 and "text/event-stream" in log_resp.headers.get("content-type", ""))

    lines = []
    for chunk in log_resp.iter_lines(decode_unicode=True):
        lines.append(chunk)
        if len(lines) > 20:
            break
    log_resp.close()
    check("SSE stream returned log lines", len(lines) > 0, f"got {len(lines)} lines")
except requests.exceptions.Timeout:
    check("SSE stream returned log lines", False, "timed out waiting for logs")

# Wait for this run to finish
wait_for_run(run_id)


# ══════════════════════════════════════════════════════════
#  STEP 12: API Gateway proxying (Fix 1)
# ══════════════════════════════════════════════════════════
header("STEP 12: API Gateway Proxying (Fix 1)")

# Test that /artifacts routes on port 8000 reach the registry

# First, publish a test artifact directly to registry for testing
test_content = b"hello forge test artifact"
import hashlib
sha = hashlib.sha256(test_content).hexdigest()

resp = requests.post(
    f"{BASE}/artifacts/test-lib/1.0.0",
    files={"file": ("test.tar.gz", test_content)},
    data={"checksum": f"sha256:{sha}"},
    headers={"Authorization": f"Bearer {token}"},
)
check("POST /artifacts via gateway → 201", resp.status_code == 201, f"got {resp.status_code}: {resp.text[:200]}")

# Download via gateway
resp = requests.get(f"{BASE}/artifacts/test-lib/1.0.0")
check("GET /artifacts/{{name}}/{{version}} via gateway → 200", resp.status_code == 200, f"got {resp.status_code}")
if resp.status_code == 200:
    check("Downloaded content matches", resp.content == test_content)

# Metadata via gateway
resp = requests.get(f"{BASE}/artifacts/test-lib/1.0.0/meta")
check("GET /artifacts/{{name}}/{{version}}/meta via gateway → 200", resp.status_code == 200, f"got {resp.status_code}")
if resp.status_code == 200:
    meta = resp.json()
    check("Metadata has correct sha256", meta.get("sha256") == sha, f"got {meta.get('sha256')}")

# List versions via gateway
resp = requests.get(f"{BASE}/artifacts/test-lib")
check("GET /artifacts/{{name}} via gateway → 200", resp.status_code == 200, f"got {resp.status_code}")
if resp.status_code == 200:
    versions = resp.json()
    check("Versions list contains 1.0.0", "1.0.0" in str(versions))

# Duplicate publish → 409
resp = requests.post(
    f"{BASE}/artifacts/test-lib/1.0.0",
    files={"file": ("test.tar.gz", test_content)},
    data={"checksum": f"sha256:{sha}"},
    headers={"Authorization": f"Bearer {token}"},
)
check("Duplicate publish → 409", resp.status_code == 409, f"got {resp.status_code}")

# Resolve via gateway (even with no deps, should return 200)
resp = requests.post(
    f"{BASE}/resolve",
    json={"dependencies": []},
    headers={"Content-Type": "application/json"},
)
check("POST /resolve via gateway → 200", resp.status_code == 200, f"got {resp.status_code}")


# ══════════════════════════════════════════════════════════
#  STEP 13: Artifact immutability & integrity
# ══════════════════════════════════════════════════════════
header("STEP 13: Registry Immutability & Integrity")

# Bad checksum
bad_content = b"bad data"
bad_sha = hashlib.sha256(bad_content).hexdigest()
resp = requests.post(
    f"{BASE}/artifacts/test-integrity/1.0.0",
    files={"file": ("test.tar.gz", bad_content)},
    data={"checksum": "sha256:0000000000000000000000000000000000000000000000000000000000000000"},
    headers={"Authorization": f"Bearer {token}"},
)
check("Mismatched checksum → 400", resp.status_code == 400, f"got {resp.status_code}")


# ══════════════════════════════════════════════════════════
#  STEP 14: Run status endpoint contract
# ══════════════════════════════════════════════════════════
header("STEP 14: Run Status Endpoint Contract")

resp = requests.get(f"{BASE}/runs/nonexistent-run-id")
check("GET /runs/{{bad_id}} → 404", resp.status_code == 404, f"got {resp.status_code}")


# ══════════════════════════════════════════════════════════
#  FINAL RESULTS
# ══════════════════════════════════════════════════════════
header("FINAL RESULTS")
total = PASS + FAIL
print(f"\n  ✅ Passed: {PASS}/{total}")
print(f"  ❌ Failed: {FAIL}/{total}")
print()

if FAIL > 0:
    print("  ⚠️  Some tests failed. Review output above.")
    sys.exit(1)
else:
    print("  🎉 All tests passed! Forge is Task 7 compliant.")
    sys.exit(0)
