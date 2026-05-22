FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y docker.io
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY engine/ ./engine/
COPY config.yaml .
CMD ["uvicorn", "engine.main:app", "--host", "0.0.0.0", "--port", "8000"]