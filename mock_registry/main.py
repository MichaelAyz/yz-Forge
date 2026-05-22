from fastapi import FastAPI
from fastapi.responses import Response
import hashlib

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok", "service": "mock-registry"}


@app.post("/resolve")
def resolve(body: dict):
    """
    Fake resolver — just returns an empty lockfile.
    Means the pipeline has no dependencies to pull.
    """
    return {"packages": []}


@app.post("/artifacts/{name}/{version}")
def publish(name: str, version: str):
    """
    Fake publish — always accepts.
    """
    return {"status": "published", "name": name, "version": version}


@app.get("/artifacts/{name}/{version}")
def download(name: str, version: str):
    """
    Fake download — returns dummy bytes.
    """
    content = b"fake artifact content"
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"X-Artifact-SHA256": hashlib.sha256(content).hexdigest()}
    )


@app.get("/artifacts/{name}/{version}/meta")
def meta(name: str, version: str):
    return {
        "name": name,
        "version": version,
        "sha256": "abc123",
        "size": 100,
        "deps": [],
        "published_at": "2024-01-01T00:00:00Z"
    }


@app.get("/artifacts/{name}")
def list_versions(name: str):
    return {"versions": ["1.0.0"]}