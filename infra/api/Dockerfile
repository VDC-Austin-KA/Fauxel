# syntax=docker/dockerfile:1
# Fauxel API / worker / relay image. Build context is the REPO ROOT (compose: context "..", dockerfile infra/api/Dockerfile).
# One image, three commands: uvicorn (api), `python -m fauxel_api.worker`, `python -m fauxel_api.relay`.
#
# Build args
#   WITH_GPU=1      onnxruntime-gpu instead of onnxruntime (docker-compose.gpu.yml sets it; needs the NVIDIA runtime to use it)
#   WITH_SPECKLE=0  skip specklepy (Speckle send/receive endpoints then answer 501)
# Behind a TLS-intercepting proxy pass the CA as a build secret (compose does this via BUILD_CA_BUNDLE), never disable TLS.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY services/capture-ingestion /app/services/capture-ingestion
COPY services/api /app/services/api
COPY connectors/aps-forma /app/connectors/aps-forma
COPY connectors/ifc-speckle-bcf /app/connectors/ifc-speckle-bcf
COPY db /app/db
COPY contracts /app/contracts
ARG WITH_GPU=0
ARG WITH_SPECKLE=1
RUN --mount=type=secret,id=ca,required=false \
    if [ -s /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca SSL_CERT_FILE=/run/secrets/ca REQUESTS_CA_BUNDLE=/run/secrets/ca; fi; \
    EXTRA=""; [ "$WITH_SPECKLE" = "1" ] && EXTRA="/app/connectors/ifc-speckle-bcf[speckle]" || EXTRA="/app/connectors/ifc-speckle-bcf"; \
    pip install /app/services/capture-ingestion /app/connectors/aps-forma "$EXTRA" "/app/services/api[kafka]" \
    && if [ "$WITH_GPU" = "1" ]; then \
         pip install onnxruntime-gpu nvidia-cudnn-cu12 nvidia-cublas-cu12 nvidia-cuda-runtime-cu12 nvidia-cufft-cu12 nvidia-curand-cu12 nvidia-cuda-nvrtc-cu12 \
         && ls -d /usr/local/lib/python3.12/site-packages/nvidia/*/lib > /etc/ld.so.conf.d/nvidia-pip.conf && ldconfig; \
       else pip install onnxruntime; fi
ENV FAUXEL_MIGRATIONS_DIR=/app/db/migrations FAUXEL_CONTRACTS_DIR=/app/contracts FAUXEL_MODEL_DIR=/models
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=40s --retries=6 \
  CMD curl -fsS http://127.0.0.1:8000/api/v1/health >/dev/null || exit 1
# Migrations run on start (FAUXEL_AUTO_MIGRATE=1, advisory-locked, so api/worker/relay can start together).
CMD ["uvicorn", "fauxel_api.app:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
