FROM ghcr.io/astral-sh/uv:0.11.26 AS uv

FROM python:3.12-slim AS builder

WORKDIR /app
COPY --from=uv /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra mcp --no-install-project

COPY argus/ ./argus/
RUN uv sync --frozen --no-dev --extra mcp

COPY scripts/build_runtime_manifest.py ./scripts/build_runtime_manifest.py
ARG VCS_REF
RUN python scripts/build_runtime_manifest.py \
    --output /app/runtime-manifest.json \
    --source-revision "${VCS_REF}" \
    --lock-file uv.lock

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 libxslt1.1 && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/argus /app/argus
COPY --from=builder /app/runtime-manifest.json /app/runtime-manifest.json

ENV PATH="/app/.venv/bin:${PATH}" \
    ARGUS_RUNTIME_MANIFEST=/app/runtime-manifest.json

RUN argus image-admission --manifest /app/runtime-manifest.json

RUN useradd -m -s /bin/sh argus && chown -R argus:argus /app
USER argus

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

CMD ["uvicorn", "argus.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
