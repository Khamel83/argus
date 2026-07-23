FROM ghcr.io/astral-sh/uv:0.11.26@sha256:3d868e555f8f1dbc324afa005066cd11e1053fc4743b9808ca8025283e65efa5 AS uv

FROM python:3.12.13-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b AS builder

WORKDIR /app
COPY --from=uv /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md ./
COPY alembic.ini ./
COPY migrations/ ./migrations/
RUN uv sync --frozen --no-dev --extra mcp --no-install-project

COPY argus/ ./argus/
RUN uv sync --frozen --no-dev --extra mcp

COPY scripts/build_runtime_manifest.py ./scripts/build_runtime_manifest.py
ARG VCS_REF
RUN /app/.venv/bin/python scripts/build_runtime_manifest.py \
    --output /app/runtime-manifest.json \
    --source-revision "${VCS_REF}" \
    --lock-file uv.lock

FROM python:3.12.13-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/argus /app/argus
COPY --from=builder /app/uv.lock /app/uv.lock
COPY --from=builder /app/runtime-manifest.json /app/runtime-manifest.json
COPY alembic.ini ./
COPY migrations/ ./migrations/

ENV PATH="/app/.venv/bin:${PATH}" \
    ARGUS_RUNTIME_MANIFEST=/app/runtime-manifest.json

ARG IMAGE_ADMISSION_POLICY=production
RUN case "${IMAGE_ADMISSION_POLICY}" in \
        production) argus image-admission --manifest /app/runtime-manifest.json ;; \
        development) argus image-admission --manifest /app/runtime-manifest.json --allow-development-revision ;; \
        *) echo "invalid IMAGE_ADMISSION_POLICY: ${IMAGE_ADMISSION_POLICY}" >&2; exit 1 ;; \
    esac

RUN useradd -m -s /bin/sh argus \
    && mkdir -p /var/lib/argus \
    && chown -R argus:argus /app /var/lib/argus
USER argus

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

CMD ["uvicorn", "argus.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
