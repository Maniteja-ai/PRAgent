# PRAgent — presentation and interview guide

Use this as a short code tour. Open the root [README](../../README.md), then the files below in order. Keep the two diagrams visible: [agent flow](assets/agent-code-design-flow.png) and [ingestion flow](assets/ingestion-code-flow.png).

## A simple 5-minute explanation

> PRAgent has two parts. Ingestion builds reusable knowledge from documentation, source code and configured UI observations. It stores searchable text and embeddings in Qdrant, and explicit code and UI relationships in Neo4j. When GitHub sends a pull-request event, a worker fetches the PR, retrieves evidence, runs configured checks, asks Gemini to explain likely impact, validates the result, and posts or updates one report comment. The report cites evidence and names gaps instead of claiming tests passed when they did not.

## Code tour

1. **Start with the repository README.** Show the architecture image and the commands to run ingestion and the local webhook service.
2. **Explain ingestion.** Open `ingestion/src/ingestion/cli.py` for the command entry point, then `ingestion/src/ingestion/pipeline/ingestion_pipeline.py` for orchestration. The six focused JSON files separate source inputs, models, chunking, storage, limits and evaluation. The bootstrap loads and validates configuration before injecting the chosen implementations.
3. **Explain startup and dependency wiring.** Open `agents/src/impact_agent/dependencies/agent_bootstrap.py`. It loads typed settings and assembles adapters once. The FastAPI route in `agents/src/impact_agent/webhook/implementations/fastapi_app.py` receives webhook requests; `github_receiver.py` verifies and validates them before the durable queue accepts work.
4. **Show the coordinator.** Open `agents/src/impact_agent/pipeline/implementations/langgraph_agent_pipeline.py`. The graph makes the stages explicit: validate, fetch, retrieve, explore UI, analyze, verify behavior, and create the report.
5. **Show retrieval and safety.** `agents/src/impact_agent/tools/knowledge/implementations/composite_knowledge_retriever.py` combines retriever evidence. Qdrant finds text by semantic similarity; Neo4j supplies explicit, revision-scoped code relationships and supported mappings. The graph-query planner asks Gemini for schema-aware Cypher, while the graph adapter constrains it to validated read-only queries with configured row/depth limits.
6. **Show the result.** Open `agents/evaluation/agent_quality/snapshots/2026-10-05-saleor-pr-1/report.md` and `summary.md`. Then show `docs/project/sample-pr-impact-report.md` and `ingestion/docs/retrieval-experiment-history.md` for sample output and retrieval trade-offs.

## What the LangGraph state carries

`AgentState` is a typed dictionary shared between stages. It carries:

- `run_id` — identifies this analysis and its history;
- `request` — the repository and PR number supplied by the event;
- `pull_request` — fetched PR metadata, changed files and diff;
- `evidence` — retrieved and direct-diff evidence, with source IDs and hashes;
- `decision` — the model's evidence-cited impact findings;
- `behavior_results` — checks actually executed and their outcomes;
- `gaps` — work not performed or evidence not available;
- `report` — the final status and rendered result.

Each node returns only the state fields it changes. `PipelineNode` is an enum for stable graph node names; it avoids scattering misspelled string literals through the graph. `EvaluationRecordingDecorator` wraps each stage and records status, duration, and SHA-256 fingerprints without saving the full state content.

## Design choices to point out

| Pattern | Where it appears | Why it helps |
| --- | --- | --- |
| Ports and adapters | Interfaces in `*/interface/`; concrete GitHub, Gemini, Neo4j, Qdrant and Playwright implementations in `*/implementations/` | Provider changes do not require rewriting the workflow. |
| Constructor injection / composition root | `AgentBootstrap` and ingestion `bootstrap.py` | Dependencies are selected once and passed in explicitly, which makes stages easier to test. |
| Strategy and factory | Provider factories select implementations from validated JSON settings | New providers can be added behind the same contract. |
| Decorator | `EvaluationRecordingDecorator` around pipeline stages | Captures audit timing and fingerprints without mixing instrumentation into business logic. |
| State graph | `LangGraphAgentPipeline` and typed `AgentState` | Makes stage order and data passed between stages visible. |
| Repository-style persistence port | `RunStore` and its SQLite implementation | Workflow code does not depend directly on SQLite details. |

## Guardrails and model calls

The current guardrails are deterministic checks in `BasicGuardrail`: validate PR identifiers, bound evidence size, verify evidence hashes, screen for selected prompt-injection patterns, redact configured sensitive strings, and require findings to cite known evidence (including changed-code evidence where available). The same output check runs before a report is saved or published. These checks do not prove that an LLM conclusion is true; they make evidence handling and report claims more constrained.

Gemini is used for graph-query planning and impact analysis. The graph query is checked and limited before execution. A separate LLM safety classifier is not currently used: deterministic controls avoid extra model calls and the associated latency/cost, but they do not provide organization-grade DLP or comprehensive prompt-injection detection. Describe this as a current boundary, not as complete security.

## Evaluations and reranking

The project has separate data and runners for retrieval and agent report quality. Precision asks how many returned items are relevant; recall asks how many relevant items were found; report judges also score grounding/faithfulness and relevance against reference labels. Results are diagnostic while the datasets remain small or draft; a single LLM-judge score is not a production accuracy claim. Always show the dataset status and sample count with a score.

Reranking was measured as an optional retrieval experiment, not enabled by default in the PR workflow. On the frozen 40-query comparison, Gemini reranking achieved 97.14% Precision@1 and 100% selected relevant-passage recall; the tested MiniLM cross-encoder achieved 77.14% and 70.45%, respectively. Those measurements apply only to that dataset, model and selection rule. Plain vector retrieval remains the default to keep the runtime simpler and less costly; the full measurements and caveats are in `ingestion/docs/retrieval-experiment-history.md`.

## Questions you may be asked

**Why both Qdrant and Neo4j?** Qdrant answers “which text is semantically relevant?” Neo4j answers “which explicit, typed code relationships connect these files, symbols, routes and requirements?” Similarity is not proof of a dependency.

**Does the agent merge code or change the PR?** No. It analyzes evidence and publishes a report comment; it does not modify source code or merge a pull request.

**What happens when a stage cannot verify something?** The pipeline records a gap and can finish as `COMPLETED_WITH_GAPS`. The report separates likely impact from checks that actually ran.

**Is the UI crawler autonomous?** No. UI exploration and behavior checks use configured, bounded browser adapters and journeys. Unsupported mappings and untested interactions remain gaps.

**Why use webhooks and a queue?** The webhook validates and acknowledges an event quickly; a durable worker performs slower analysis and can retry bounded failures.

**What remains before production?** Broader independently reviewed datasets, end-to-end coverage across more PR types, stronger DLP/privacy controls, deployment and webhook operations, and load/security testing.
