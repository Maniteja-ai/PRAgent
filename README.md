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

Latest local verification: **87 agent tests** and **28 ingestion tests** pass. Automated tests mostly mock external services; the separate live PR run described below exercises configured external integrations for one PR but is not broad production validation.

For ingestion, configure `ingestion/.env` and review `ingestion/configs/saleor.json` plus the six files it references before running:

```powershell
cd ingestion
uv run ingest configs/saleor.json
```

That command may call configured model providers and write to the configured stores. The agent's webhook app is assembled by `AgentBootstrap` in `agents/src/impact_agent/dependencies/agent_bootstrap.py`; set up the external ASGI host and webhook configuration before exposing it.

## Evaluation

- The ingestion retrieval dataset is in `ingestion/evaluation/datasets/saleor-retrieval-v0.1/` and is still a draft pending human review.
- The saved PR-report evaluator is in `agents/evaluation/agent_quality/`. Its current one-case LLM-judge result is diagnostic, not a validated benchmark.

The latest live run is `live-e2e-pr1-scope-gap-20261005` for `Maniteja-ai/storefront#1`. It loaded GitHub PR data, queried Gemini, Neo4j and Qdrant, ran the configured Playwright smoke checks, and produced a report with a coverage gap. It verified the checkout URL and the presence of the discount-code input and Apply button; it did **not** submit a code, verify invalid-code handling, remove a voucher, or compare totals. Its one-case judge scores are draft diagnostics.

Assessment deliverables:

- [Design document](docs/assessment/design-document.md)
- [QA-facing sample PR impact report](docs/assessment/sample-pr-impact-report.md)
- [5–10 minute demo runbook](docs/assessment/demo-runbook.md)

Known limitations include incomplete requirement-to-UI/code relationships, configured Playwright journeys rather than autonomous exploration, and a draft one-case evaluation dataset. The current live report has direct PR-diff citations and makes the smoke-test scope explicit. See the component READMEs and assessment documents for details.
