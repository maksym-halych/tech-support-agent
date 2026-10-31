# tech-support-agent

PolyTech Code Fest Hackathon Project

A conversational tech support agent: a LangGraph agent served by Agent Server, with a Next.js chat frontend.

## Layout

```
agent/                      LangGraph agent (Python 3.13, uv)
  langgraph.json            Agent Server config: graph "agent" -> tech_support_agent.graph:build_agent
  src/tech_support_agent/
    graph.py                create_agent wiring: model, prompt, tools, middleware
    config.py               Settings from env / agent/.env
    prompts.py              System prompt
    middleware/             Per-turn tool call limit
    tools/                  One module per tool; the tool set lives in registry.py
  tests/
frontend/                   Next.js chat UI (Agent Chat UI fork, Clerk sign-in, pnpm)
```

## Getting started

Prerequisites: [uv](https://docs.astral.sh/uv/), Node 22 and pnpm.

1. Copy `agent/.env.example` to `agent/.env` and set `OPENAI_API_KEY` and `OPENAI_BASE_URL`.
2. Copy `frontend/.env.example` to `frontend/.env` and set the Clerk keys.
3. Install and run:

```bash
make install
make agent      # LangGraph dev server on http://localhost:2024
make frontend   # Chat UI on http://localhost:3000
```

Other targets: `make test` and `make pre-commit`.
