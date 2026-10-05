# PRAgent

This folder contains the webhook-driven agent. Ingestion is maintained separately in `../ingestion/`.

## Flow

```text
GitHub PR webhook -> validate signature -> durable SQLite queue -> worker
  -> fetch PR and diff -> search Qdrant + Neo4j -> optional browser/behavior checks
  -> Gemini impact analysis -> guardrails -> Markdown report + SQLite run history
```

`src/impact_agent/dependencies/agent_bootstrap.py` is the startup entry point. `AgentBootstrap.create(...)` loads and validates the separate JSON settings, builds the concrete adapters, and returns the webhook app, worker, and services. The LangGraph stage order is in `src/impact_agent/pipeline/implementations/langgraph_agent_pipeline.py`.

## Run locally and receive a PR

Use Python 3.11–3.14 and `uv`. From this directory, install the agent and create its local environment file:

```powershell
uv sync --extra dev
Copy-Item .env.example .env   # only the first time
```

Edit `.env` and set `GITHUB_TOKEN`, `GITHUB_WEBHOOK_SECRET`, `GEMINI_API_KEY`, `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, and `NEO4J_DATABASE`. The token needs to read pull requests and create/update issue comments in the target repository. Qdrant is read from the sibling ingestion store by default, so no Qdrant credentials are needed for this local setup. Never commit `.env`.

Install the Playwright browser once, then start the API and worker:

```powershell
uv run playwright install chromium
uv run python run_local.py
```

The API listens on `127.0.0.1:8000`; check `http://127.0.0.1:8000/health`. GitHub cannot call a loopback address, so expose port 8000 using a public HTTPS tunnel (for example `ngrok http 8000`). Add a webhook to the target GitHub repository with URL `<tunnel-url>/webhooks/github`, content type `application/json`, the same webhook secret, and the **Pull requests** event. `webhook.json` accepts `opened`, `synchronize`, and `reopened`. Open a fresh test PR to trigger a run. Keep the server and tunnel running until the report comment appears; use `Ctrl+C` to stop the local server.

The webhook is acknowledged and queued first; the worker then runs the agent pipeline asynchronously. Reports and stage history are stored in `data/agent-runs.sqlite3`; webhook deliveries and retries are stored in `data/webhook-jobs.sqlite3`. GitHub PR comments are created on the first run and updated on later runs.

## Configuration

Each file under `config/default/` has one purpose, with a matching validator under `src/impact_agent/config/validation/`:

| File | Purpose |
| --- | --- |
| `agent.json` | Which analysis stages are enabled |
| `github.json` | GitHub API, changed-file limit, diff size and citable-code-evidence budget |
| `webhook.json` | Accepted events, signature secret, request size and queue database |
| `models.json` | Decision model and embedding model settings |
| `knowledge.json` | Qdrant project, collection and retrieval limit |
| `graph_database.json` | Neo4j connection, query row/time limits, schema refresh and call depth |
| `browser.json` | Browser host and timeout policy |
| `behavior.json` | Explicit browser actions and assertions for behavior checks |
| `runtime.json` | Call, retry, timeout and worker limits |
| `guardrails.json` | Evidence, instruction and sensitive-value handling |
| `run_history.json` | SQLite report history location |
| `evaluation.json` | Stage recorder and golden dataset location |

Secrets are read from environment variables and are never placed in JSON. `agent.json` points to the local `.env` file; the bootstrap loads it without replacing values already supplied by the host environment. `.env` is ignored by Git. `.env.example` lists variable names without secret values. The agent uses the same local Qdrant store as ingestion at `../ingestion/.vector-store`, so this setup needs no Qdrant URL or API key. Remote Qdrant can be configured with `QDRANT_URL` and, when required by that service, `QDRANT_API_KEY`. Set `GITHUB_TOKEN`, `GITHUB_WEBHOOK_SECRET`, `GEMINI_API_KEY`, `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, and `NEO4J_DATABASE` for the default live configuration. The default decision model is `gemini-3.8-flash`; `models.json` also holds the separate Gemini embedding model setting.

## Saleor graph and browser coverage

The Saleor code/UI graph was refreshed on 4 October 2026 with
`../ingestion`: 268 code files, 596 import relationships and four observed route mappings are
stored in Neo4j. The extractor resolves TypeScript `@/...` aliases and dynamic imports, so a change
in the checkout view can be traced to `src/app/checkout/page.tsx` and its observed `/checkout` URL.
Graph retrieval is schema-aware: before each PR query, the agent reads Neo4j's node and
relationship labels/properties, then Gemini generates a parameterized Cypher query from that schema
and the PR context. The agent validates the query, requires PR-path/revision parameters and bounded
result columns, and executes it in a read-mode transaction with a timeout and row limit. Keep the
Neo4j credentials configured in `.env` on a dedicated read-only database user; the read-mode session
is routing policy, while database permissions provide the write-prevention boundary. Schema is
cached for the configured `schema_cache_seconds`. Missing graph data still appears as an explicit
coverage gap. Query planning is an additional Gemini call per PR; it uses the model configured in
`models.json`.

Code indexing stores files and symbols in Neo4j with typed `IMPORTS`, `DECLARES`, `CALLS`,
`RENDERS`, `EXTENDS`, and `IMPLEMENTS` edges when the TypeScript parser can resolve them. Retrieval
returns related symbol names and source line ranges, with `graph_database.json` setting the maximum
call-chain depth. Qdrant code chunks are split by function, method, component, or class context and
carry symbol and line metadata alongside source text. These changes take effect in the hosted stores
only after a code-index refresh. Run that refresh from the sibling `ingestion/` directory with
`uv run ingest configs/saleor.json --code-index-only`; it uses the configured embedding provider and
updates the vector and graph stores. Use `--code-ui-only` for graph and browser-route refreshes that
do not make embedding calls.

The mappings currently prove **which page route is connected to the code**, not which exact controls
appear on that page. Behavior scenarios are selected automatically: a scenario runs only when a PR
changed file has a confirmed Neo4j mapping to the scenario's route. No PR-specific file patterns are
required in `behavior.json`. If a changed file maps to a route with no configured scenario, the run
records a coverage gap for that route only. The checkout scenario adds a product, opens checkout, and
checks that the discount input, Apply button, and checkout URL are present. It does not submit or
remove a voucher, validate an error, or verify changed totals. The verifier reports this smoke-test
scope as a coverage gap; the report lists each assertion that passed. Browser navigation and redirects
are restricted to the HTTPS host allowlist in `browser.json`.

Refresh the code and UI graph without making LLM, embedding or Qdrant calls from the sibling folder:

```powershell
cd ..\ingestion
uv run ingest configs/saleor.json --code-ui-only
```

The local-run instructions above install the browser binary with `uv run playwright install chromium`.

## Run checks

From this folder, install the development dependencies and run the offline test suite:

```powershell
uv sync --extra dev
uv run ruff check .
uv run mypy src
uv run pytest -q
```

The automated tests mock external services. A live end-to-end run on a sample storefront pull request
(`live-e2e-pr1-scope-gap-20261005`) exercised GitHub PR loading, Gemini query planning, Neo4j and
Qdrant retrieval, Gemini impact analysis, and the configured Playwright checks. The run completed
with a coverage gap because the browser scenario checks control presence and URL only. The report
and one-case LLM-judge evaluation are preserved in
`evaluation/agent_quality/snapshots/2026-10-05-saleor-pr-1/`. Precision, recall, and faithfulness
scored 100% for this draft case; evidence relevance scored 56.2%. The judge marked browser-scope
reporting honest.
The dataset remains `DRAFT_PENDING_HUMAN_REVIEW`; review the labels before treating these scores as a
benchmark.
