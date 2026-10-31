.PHONY: help install api agent export-openapi frontend test lint pre-commit

help:
	@echo 'Targets:'
	@echo '  install         Install the API and agent dependencies'
	@echo '  api             Start the lessons API on port 8001'
	@echo '  agent           Start the support chat in the terminal'
	@echo '  export-openapi  Refresh the agent example OpenAPI document from the API'
	@echo '  frontend        Start the frontend (orchestrator connection pending)'
	@echo '  test            Run all tests'
	@echo '  lint            Run ruff over both Python projects'
	@echo '  pre-commit      Run all pre-commit hooks against every file'

install:
	uv sync --directory api
	uv sync --directory agent

api:
	uv run --directory api lessons-api

agent:
	uv run --directory agent support-chat

export-openapi:
	uv run --directory api lessons-api-export-openapi --output ../agent/examples/lessons.openapi.json

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
