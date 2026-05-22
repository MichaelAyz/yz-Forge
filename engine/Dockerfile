FROM python:3.11-slim

WORKDIR /app

# Install Docker CLI so the engine can spin up containers
RUN apt-get update && apt-get install -y \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY engine/ ./engine/
COPY config.yaml .

# Create directories the engine needs
RUN mkdir -p /app/data/logs

CMD ["uvicorn", "engine.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]