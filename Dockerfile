FROM python:3.12-slim AS builder

WORKDIR /app

# Install build deps (needed for psycopg2-binary, lxml, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libxml2-dev libxslt1-dev && \
    rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --no-cache-dir --prefix=/install ".[mcp]"

# --- Final image ---
FROM python:3.12-slim

WORKDIR /app

# Runtime deps for lxml
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 libxslt1.1 && \
    rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application code
COPY argus/ ./argus/

# Create non-root user
RUN useradd -m -s /bin/sh argus && chown -R argus:argus /app
USER argus

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

CMD ["uvicorn", "argus.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
