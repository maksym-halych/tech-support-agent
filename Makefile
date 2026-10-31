.PHONY: help install api agent frontend test lint pre-commit

help:
	@echo 'Targets:'
	@echo '  install         Install the API and agent dependencies'
	@echo '  api             Start the lessons API on port 8001'
	@echo '  agent           Start Agent Server on port 2024'
	@echo '  frontend        Start the frontend on port 3000'
	@echo '  test            Run all tests'
	@echo '  lint            Run ruff over both Python projects'
	@echo '  pre-commit      Run all pre-commit hooks against every file'

install:
	uv sync --directory api
	uv sync --directory agent

api:
	uv run --directory api lessons-api

agent:
	uv run --directory agent langgraph dev --no-browser

frontend:
	pnpm -C frontend dev

test:
	uv run --directory api python -m pytest tests -q
	uv run --directory agent python -m pytest tests -q

lint:
	uv run --directory api ruff check src tests
	uv run --directory api ruff format --check src tests
	uv run --directory agent ruff check src tests
	uv run --directory agent ruff format --check src tests

# --project, not --directory: pre-commit resolves .pre-commit-config.yaml and
# the file list from the working directory, so it has to stay at the repo root
# while still using the agent project's environment.
pre-commit:
	uv run --project agent pre-commit run --all-files
