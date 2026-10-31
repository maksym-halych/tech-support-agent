.PHONY: help install agent frontend test pre-commit

help:
	@echo 'Targets:'
	@echo '  install      Install agent and frontend dependencies'
	@echo '  agent        Start the local LangGraph dev server'
	@echo '  frontend     Start the Agent Chat UI against the local dev server'
	@echo '  test         Run all tests'
	@echo '  pre-commit   Run all pre-commit hooks against every file'

install:
	uv sync --directory agent
	pnpm -C frontend install

agent:
	uv run --directory agent langgraph dev

frontend:
	pnpm -C frontend dev

test:
	uv run --directory agent python -m pytest tests -q

# --project, not --directory: pre-commit resolves .pre-commit-config.yaml and
# the file list from the working directory, so it has to stay at the repo root
# while still using the agent project's environment.
pre-commit:
	uv run --project agent pre-commit run --all-files
