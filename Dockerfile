FROM python:3.12-slim AS base

WORKDIR /app

# Install dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir . && pip install --no-cache-dir 'mcp>=1.0.0'

# Copy application
COPY argus/ ./argus/

# Create non-root user
RUN useradd -m -s /bin/sh argus && chown -R argus:argus /app
USER argus

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

CMD ["uvicorn", "argus.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
