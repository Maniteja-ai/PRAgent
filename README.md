# Testsigma PR Impact Agent

A two-part prototype for collecting application knowledge and analyzing the likely impact of a GitHub pull request.

## Components

- `ingestion/` loads public product and API documentation, source code, and configured browser observations. It creates searchable Qdrant content and code/UI records in Neo4j.
- `agents/` receives GitHub pull request events, fetches PR changes, retrieves related evidence, runs configured checks, and produces an impact report.

The two components have separate dependencies, configuration, and tests. Credentials belong in local `.env` files copied from each component's `.env.example`; never commit those files.

## Setup and tests

Use Python 3.11 or newer and `uv`.

```powershell
cd agents
uv sync --extra dev
uv run pytest -q

cd ..\ingestion
uv sync --group dev
uv run pytest -q
```

The current local verification is 82 agent tests and 28 ingestion tests. Automated tests mock external services; they do not by themselves prove live Neo4j, Qdrant, browser, or GitHub behavior.

For ingestion, configure `ingestion/.env` and review `ingestion/configs/saleor.json` plus the six files it references before running:

```powershell
cd ingestion
uv run ingest configs/saleor.json
```

That command may call configured model providers and write to the configured stores. The agent's webhook app is assembled by `AgentBootstrap` in `agents/src/impact_agent/dependencies/agent_bootstrap.py`; set up the external ASGI host and webhook configuration before exposing it.

## Evaluation

- The ingestion retrieval dataset is in `ingestion/evaluation/datasets/saleor-retrieval-v0.1/` and is still a draft pending human review.
- The saved PR-report evaluator is in `agents/evaluation/agent_quality/`. Its current one-case LLM-judge result is diagnostic, not a validated benchmark.

Known limitations include incomplete requirement-to-UI/code graph links, configured Playwright journeys rather than autonomous exploration, and report claims that need stronger code citations and narrower statements about behavior actually tested. See the component READMEs and evaluation notes for details.
