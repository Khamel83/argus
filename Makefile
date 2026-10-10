.PHONY: install test lint typecheck format clean

install:
	uv sync --frozen --extra dev --extra mcp

test:
	uv run --extra dev --extra mcp pytest tests/ -v --tb=short

lint:
	uv run ruff check argus tests scripts

typecheck:
	uv run python -m compileall -q argus

format:
	uv run ruff format argus tests scripts

clean:
	rm -rf .venv .pytest_cache .ruff_cache .mypy_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
