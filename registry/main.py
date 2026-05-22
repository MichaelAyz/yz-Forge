from fastapi import FastAPI
app = FastAPI()

# TODO Person 1: import and wire up routes from
# storage.py, metadata.py, resolver.py, auth.py

@app.get("/health")
def health():
    return {"status": "ok", "service": "registry"}

# POST /artifacts/{name}/{version}
# GET  /artifacts/{name}/{version}
# GET  /artifacts/{name}/{version}/meta
# GET  /artifacts/{name}