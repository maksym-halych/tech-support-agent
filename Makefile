.PHONY: help install agent demo-api export-openapi frontend test lint pre-commit

help:
	@echo 'Targets:'
	@echo '  install      Install Python agent and demo dependencies'
	@echo '  agent        Start the support chat in the terminal'
	@echo '  demo-api     Start the local booking API on port 8001'
	@echo '  export-openapi  Refresh the demo OpenAPI document'
	@echo '  frontend     Start the frontend (orchestrator connection pending)'
	@echo '  test         Run all tests'
	@echo '  pre-commit   Run all pre-commit hooks against every file'

install:
	uv sync --directory agent

agent:
	uv run --directory agent support-chat

demo-api:
	uv run --directory agent support-demo-api

export-openapi:
	uv run --directory agent support-export-openapi

frontend:
	pnpm -C frontend dev

test:
	uv run --directory agent python -m pytest tests -q

lint:
	uv run --directory agent ruff check src tests
	uv run --directory agent ruff format --check src tests

# --project, not --directory: pre-commit resolves .pre-commit-config.yaml and
# the file list from the working directory, so it has to stay at the repo root
# while still using the agent project's environment.
pre-commit:
	uv run --project agent pre-commit run --all-files
