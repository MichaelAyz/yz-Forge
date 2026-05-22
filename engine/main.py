from fastapi import FastAPI
app = FastAPI()

# TODO Person 2: import and wire up routes from
# parser.py, scheduler.py, runner.py, logs.py

@app.get("/health")
def health():
    return {"status": "ok", "service": "engine"}

# POST /runs
# GET  /runs/{id}
# GET  /runs/{id}/lockfile
# GET  /runs/{id}/logs