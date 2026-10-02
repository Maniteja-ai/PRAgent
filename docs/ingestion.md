# Reusable ingestion module

## Boundaries

`trace_impact` is application-independent. Saleor is configured in `projects/saleor/project.json`; the original `configs/saleor.json` remains the deployment/evaluation manifest. `projects/example/` is a clearly synthetic second application used to verify portability.

| Module | Responsibility | Replace or extend when |
|---|---|---|
| `models.py` | Versioned project, document, requirement, and run contracts | Adding an input field or compatible artifact version |
| `documents.py` | Allowlisted HTTPS/local reads, snapshots, HTML/Markdown normalization, section chunks | Supporting a new document format or page layout |
| `extraction.py` | Extractor protocol, optional OpenAI adapter, citation checks, exact-field deduplication | Adding another LLM provider |
| `pipeline.py` | Collection, extraction cache, progress, partial-failure handling | Adding orchestration or scheduling |
| `graph.py` | Parameterized Neo4j transactions and uniqueness constraints | Adding UI/code mapping stages |
| `cli.py` | Thin command entry points | Adding a service or UI can reuse library functions |

No Saleor URL, voucher name, PR ID, or checkout selector is embedded in the ingestion implementation.

## Run locally

Requires Python 3.11–3.14 and uv. From the repository root:

```sh
uv sync --locked --extra openai
uv run trace-impact validate-project projects/saleor/project.json
uv run trace-impact collect projects/saleor/project.json
```

Collection needs neither an API key nor Neo4j. The command prints a unique `run_dir`; use that exact path below. Each run retains raw files, normalized text, content hashes, source versions, section chunks, and a machine-readable inventory. Generated full-text snapshots are local under ignored `runs/`; do not republish third-party documentation without checking its license.

Copy `.env.example` to `.env` and fill the credentials locally. `doctor` prints presence flags, never credential values:

```sh
uv run trace-impact doctor
uv run trace-impact init-db
uv run trace-impact load-graph <run_dir>
uv run trace-impact extract <run_dir> --max-chunks 200
uv run trace-impact load-graph <run_dir> --with-requirements
```

The first graph load publishes only sources and chunks. Extraction is a separate, billable LLM stage and requires an explicit `INGESTION_MODEL`. The current CLI exposes the OpenAI adapter; another provider must implement `Extractor.extract` and be wired into the CLI. `store=False` is set on Responses API calls. Schema adherence does not prove a claim is true: deterministic quote validation and human review remain necessary.

The extraction command processes every chunk up to the explicit call limit. A lower limit produces `PARTIAL`, exit code 1, and a resumable cache; it does not claim complete ingestion. Rerun against the same run directory with a sufficient limit. Cache keys include provider, model, prompt, output schema, project configuration, authority, and chunk identity. Successful cached results are reused; failed calls are retried on rerun. Separate extraction histories are retained. The adapter retries transient API failures at most twice per call. Collection failures remain explicit; extraction refuses a partial corpus.

## Neo4j setup

Use an existing Neo4j database or create an Aura Free instance in [Aura Console](https://console.neo4j.io/), named `testsigma-impact-agent`. A database instance is infrastructure; a `Project` node is our application's logical namespace. Multiple repositories can share one database using distinct project IDs. Separate databases/accounts are preferable when different tenants need security isolation; project IDs alone are not access control.

Enter the actual URI, username, password, and database in `.env`. Instance creation, account terms, and initial password handling are performed by the account owner. No paid instance is needed for the intended small prototype; check the offered tier before creating it. `init-db` verifies connectivity and creates constraints. Nothing drops or clears existing data.

The code uses the official Neo4j driver and managed write transactions. Re-loading the same run merges the same IDs and relationships. Live validation remains pending until credentials are supplied. To explicitly run the database integration test after configuring the environment:

```powershell
$env:RUN_NEO4J_INTEGRATION = "1"
uv run --env-file .env pytest -q -m integration
```

The test writes a uniquely named synthetic project and leaves it for inspection; it never wipes a shared graph.

## Add another repository

1. Copy `projects/example/` to a new project directory.
2. Replace the synthetic repository URL, placeholder commit, application URL, scope, and sources.
3. Pin the actual deployed baseline commit. Configure document hosts explicitly and label each source's authority/version.
4. Validate and collect using the same CLI. Compare the inventory with the chosen scope.
5. Select an extraction adapter and review the output before connecting UI and code observations.

Supported now: explicit public HTTPS HTML/Markdown sources and local Markdown/HTML files inside the project configuration folder. Not yet supported: arbitrary authenticated wikis, PDF parsing, automatic whole-site discovery, or private GitHub source fetching. Those require adapters, not core changes. The repository URL and code roots are inputs for the future code stage; this phase does not pretend to analyze code.

## Evidence, ambiguity, and absence

Every candidate carries actor, behavior, preconditions, expected outcome, exceptions, layer, supporting quote, and uncertainty reasons. The pipeline attaches source/chunk IDs itself. It checks exact quotes after whitespace normalization and rejects missing citations or empty behavior fields. Inferred claims and frontend claims sourced only from backend/API documents enter `NEEDS_REVIEW`. `GROUNDED_CANDIDATE` means a quote exists, not that semantic correctness was proved. Rejected candidates remain in the audit output; consumers must filter validation states.

Deduplication merges only identical structured fields and keeps evidence from all source chunks. Similar meanings with different wording are not automatically merged. Conflicting statements are not automatically resolved. Review and evaluation are necessary before treating candidates as definitive intent; a calibrated confidence score is not claimed.

Coverage is per assessment/run, not a permanent boolean on a requirement. Ingestion initializes `NOT_EVALUATED` with `INGESTION_ONLY` scope. Future crawl assessments may be `OBSERVED`, `NOT_OBSERVED`, or `BLOCKED`, with an action budget, reason and evidence. Merely seeing a control does not establish that its business behavior works. An unseen feature is not declared missing.

## Assignment requirements retained

| Assignment requirement | Current boundary |
|---|---|
| Autonomous browser exploration with DOM, screenshots, transitions | Future browser module; manual setup screenshots are not agent output |
| Parse a public spec into structured requirements | Collection and extraction code implemented; live model run requires credentials |
| Neo4j connects requirements, UI and code | Requirement provenance schema and loader implemented; live DB and other layers pending |
| Real PR blast radius for a non-engineer | Future analyzer; PR/patched evidence is excluded from ingestion inputs |
| Explain absence, ambiguity and confidence | Explicit candidate validation and per-run assessment model |
| Evaluation, scope decisions, design document and demo | Unit/integration tests and design notes begin here; full evaluation and final deliverables remain |

The manually curated `requirements/saleor.json`, PR description/diff, patched deployment, and manual validation evidence are not ingestion sources. They remain separate evaluation materials. Online Saleor backend docs are unpinned and may differ from the sandbox's version; document hashes record what was read, not a guarantee of compatibility.

## Validation

```sh
uv run ruff check src tests
uv run pytest -q
uv run trace-impact collect projects/example/project.json
```

Offline tests use labeled test doubles, not fabricated production extractions. Live source collection, live LLM extraction, and live graph roundtrips are separate checks and must be reported separately. This module does not yet include RAG, embeddings, semantic conflict detection, automated entailment verification, or a review UI.

Implementation references: [Neo4j managed transactions](https://neo4j.com/docs/python-manual/current/transactions/), [Aura instance creation](https://neo4j.com/docs/aura/getting-started/create-instance/), [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
