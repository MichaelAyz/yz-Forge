FROM python:3.11-slim
WORKDIR /app
RUN pip install fastapi uvicorn
COPY mock_registry/ ./mock_registry/
CMD ["uvicorn", "mock_registry.main:app", "--host", "0.0.0.0", "--port", "8001"]