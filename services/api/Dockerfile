# Build from the repo root:  docker build -f services/api/Dockerfile -t fauxel-api .
# CUDA later: docker build --build-arg BASE_IMAGE=nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04 ... (image must provide python3.13)
ARG BASE_IMAGE=python:3.13-slim
FROM ${BASE_IMAGE}
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY services/capture-ingestion /app/services/capture-ingestion
COPY services/api /app/services/api
COPY db/migrations /app/db/migrations
COPY contracts /app/contracts
RUN pip install /app/services/capture-ingestion && pip install "/app/services/api[kafka]"
ENV FAUXEL_MIGRATIONS_DIR=/app/db/migrations FAUXEL_CONTRACTS_DIR=/app/contracts
EXPOSE 8000
CMD ["uvicorn", "fauxel_api.app:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000"]
