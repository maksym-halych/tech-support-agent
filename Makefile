.PHONY: help install agent bug-worker bug-demo-agent bug-preflight bug-rehearsal frontend test pre-commit

help:
	@echo 'Targets:'
	@echo '  install      Install agent and frontend dependencies'
	@echo '  agent        Start the local LangGraph dev server'
	@echo '  bug-worker   Start the configured private loopback bug worker'
	@echo '  bug-demo-agent Start the public bug bridge on loopback'
	@echo '  bug-preflight Check configured model/Docker/Telegram/GitHub targets'
	@echo '  bug-rehearsal SCENARIO=continue|both LIVE=--run-live (real side effects)'
	@echo '  frontend     Start the Agent Chat UI against the local dev server'
	@echo '  test         Run all tests'
	@echo '  pre-commit   Run all pre-commit hooks against every file'

install:
	uv sync --python 3.14 --directory agent
	pnpm -C frontend install

agent:
	uv run --python 3.14 --directory agent langgraph dev

bug-worker:
	uv run --python 3.14 --directory agent python -m tech_support_agent.bug_resolution.worker

bug-demo-agent:
	uv run --python 3.14 --directory agent langgraph dev --config langgraph.bug-demo.json --host 127.0.0.1 --no-browser

bug-preflight:
	uv run --python 3.14 --directory agent python -m tech_support_agent.bug_resolution.preflight --stage all

bug-rehearsal:
	uv run --python 3.14 --directory agent python -m tech_support_agent.bug_resolution.rehearsal --scenario "$(SCENARIO)" $(LIVE)

frontend:
	pnpm -C frontend dev

test:
	uv run --python 3.14 --directory agent python -m pytest tests -q

# --project, not --directory: pre-commit resolves .pre-commit-config.yaml and
# the file list from the working directory, so it has to stay at the repo root
# while still using the agent project's environment.
pre-commit:
	uv run --python 3.14 --project agent pre-commit run --all-files
