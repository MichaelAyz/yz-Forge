import subprocess
import time
import requests
import re
import sys
import os

print("--- Validating docker compose ---")
result = subprocess.run(["docker", "compose", "config"], capture_output=True, text=True)
if result.returncode == 0:
    print("docker compose config validates cleanly. All three services defined.")
else:
    print("docker compose config failed!")
    print(result.stderr)
    sys.exit(1)

print("--- Starting services ---")
subprocess.run(["docker", "compose", "up", "-d", "--build"])
time.sleep(10) # wait for services to boot

print("--- Creating token ---")
result = subprocess.run(["python", "-m", "registry.auth", "create-token", "--publisher", "test_user"], capture_output=True, text=True)
print(result.stdout)

token = None
for line in result.stdout.split('\n'):
    if line.startswith('Token:'):
        token = line.split('Token:')[1].strip()
        break

if not token:
    print("Failed to parse token from output!")
    sys.exit(1)

# Ensure a test pipeline exists
if not os.path.exists("dummy.yaml"):
    with open("dummy.yaml", "w") as f:
        f.write("name: test\njobs:\n  test:\n    runtime: alpine\n    steps:\n      - {name: t, run: echo 1}")

print("--- Testing unauthenticated POST /runs ---")
resp = requests.post("http://localhost:8000/runs", files={"pipeline": open("dummy.yaml", "rb")})
print(f"Status Code: {resp.status_code}")
if resp.status_code == 401:
    print("Unauthenticated correctly returns 401")
else:
    print("ERROR: Unauthenticated did not return 401")

print("--- Testing authenticated POST /runs ---")
resp = requests.post("http://localhost:8000/runs", 
                     files={"pipeline": open("dummy.yaml", "rb")},
                     headers={"Authorization": f"Bearer {token}"})
print(f"Status Code: {resp.status_code}")
if resp.status_code == 201:
    print("Authenticated correctly returns 201")
else:
    print("ERROR: Authenticated did not return 201")
    print(resp.json())

print("--- Done ---")
