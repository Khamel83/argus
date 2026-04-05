FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends build-essential && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim

WORKDIR /app
COPY --from=builder /install /usr/local
COPY argus/ argus/

RUN mkdir -p /data

ENV ARGUS_ENV=production \
    ARGUS_HOST=0.0.0.0 \
    ARGUS_PORT=8000 \
    ARGUS_DB_PATH=/data/argus.db \
    ARGUS_LOG_LEVEL=INFO

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

CMD ["argus", "serve", "--host", "0.0.0.0", "--port", "8000"]
