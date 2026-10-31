.PHONY: help install api agent frontend users teacher student bug-worker bug-demo-agent bug-preflight bug-rehearsal test lint pre-commit

help:
	@echo 'Targets:'
	@echo '  install         Install the API and agent dependencies'
	@echo '  api             Start the lessons API on port 8001'
	@echo '  agent           Start Agent Server on port 2024'
	@echo '  frontend        Start the frontend on port 3000'
	@echo '  users           List Clerk users and their roles'
	@echo '  teacher         Make a Clerk user a teacher: make teacher EMAIL=...'
	@echo '  student         Make a Clerk user a student: make student EMAIL=...'
	@echo '  bug-worker      Start the configured private loopback bug worker'
	@echo '  bug-demo-agent  Start the public bug bridge on loopback'
	@echo '  bug-preflight   Check configured model/Docker/Telegram/GitHub targets'
	@echo '  bug-rehearsal   SCENARIO=continue|both LIVE=--run-live (real side effects)'
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

bug-worker:
	uv run --directory agent python -m tech_support_agent.bug_resolution.worker

bug-demo-agent:
	uv run --directory agent langgraph dev --config langgraph.bug-demo.json --host 127.0.0.1 --no-browser

bug-preflight:
	uv run --directory agent python -m tech_support_agent.bug_resolution.preflight --stage all

bug-rehearsal:
	uv run --directory agent python -m tech_support_agent.bug_resolution.rehearsal --scenario "$(SCENARIO)" $(LIVE)

frontend:
	pnpm -C frontend dev

# Roles live in Clerk public metadata; these read CLERK_SECRET_KEY from frontend/.env.
users:
	uv run --directory api lessons-api-role

teacher:
	uv run --directory api lessons-api-role "$(EMAIL)" --role teacher

student:
	uv run --directory api lessons-api-role "$(EMAIL)" --role student

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
	uv run --python 3.14 --project agent pre-commit run --all-files
