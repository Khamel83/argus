FROM ghcr.io/astral-sh/uv:0.13.10@sha256:a3ea1357342c1a4bf9a0c53a3deb1b55379aefa037338d8113d5771efa01774f AS uv

FROM mcr.microsoft.com/playwright/python:v1.65.2-noble@sha256:38dd67fc654b56b3cba611e4c8eb71cd33750e1cd29eea2a5dc00b484817a75f AS builder

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

FROM mcr.microsoft.com/playwright/python:v1.65.2-noble@sha256:38dd67fc654b56b3cba611e4c8eb71cd33750e1cd29eea2a5dc00b484817a75f

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/argus /app/argus
COPY --from=builder /app/uv.lock /app/uv.lock
COPY --from=builder /app/runtime-manifest.json /app/runtime-manifest.json
COPY alembic.ini ./
COPY migrations/ ./migrations/

ENV PATH="/app/.venv/bin:${PATH}" \
    ARGUS_RUNTIME_MANIFEST=/app/runtime-manifest.json \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    HOME=/home/argus

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
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/live')" || exit 1

CMD ["uvicorn", "argus.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
