# Retrieval reranking and evidence selection

The library now implements `Retriever → Reranker → EvidenceSelector`. Each stage has one job, and each can be replaced without editing ingestion. The existing `search` API still returns raw vector results; the new `retrieve` API adds configurable reranking and selection.

## Run it

Use an existing completely indexed ingestion run. **The default is vector retrieval returning at most five passages, with no reranking model call.** Query embedding still uses the run's configured embedding provider:

```powershell
uv run --no-sync trace-impact retrieve runs/saleor-storefront/44dd49dc16a84d2e82153ebd7d65c6ee "What is required to apply a shipping voucher?"
```

The run supplies its original embedding model, corpus, project and snapshot. [retrieval.default.json](../configs/retrieval/default.json) is the explicit equivalent of this default. Add `--config configs/retrieval/gemini.json` or a cross-encoder/compatible configuration only when you want reranking. Gemini had the best measured quality among the evaluated rerankers; that does not make it the default. Credentials remain in `.env`. Results retain source passages, original scores, selected IDs and timing. Model grades, quotes and token usage appear only when the chosen provider supplies them. With plain vector retrieval, `EVIDENCE_FOUND` means passages were returned; it does not establish that the question is answerable. Semantic coverage remains `NOT_EVALUATED`.

To keep the original ranking and return the first five results, use [retrieval.baseline.json](../configs/retrieval/baseline.json). The same pipeline executes without a reranking model call. To regenerate editor suggestions after registering custom implementations:

```powershell
uv run --no-sync trace-impact retrieval-schema
```

The schema and runtime both reject unexpected built-in option fields. Provider names ignore case and surrounding spaces. Model IDs remain case-sensitive. Built-in options are validated before constructing provider clients. JSON selects registered implementations; it cannot import arbitrary Python code.

## Separate LLM model, endpoint and key configuration

Use [retrieval.llm.json](../configs/retrieval/llm.json) for a separately configured
Gemini reranker. It uses the same currently evaluated model but reads a dedicated key
from `ENCODER_API_KEY` in your local `.env`. The real `.env` has not been modified.
You can instead set `api_key_env` to `GEMINI_API_KEY` to reuse the existing key.

```json
{
  "provider": "gemini",
  "options": {
    "model": "gemini-3.5-flash-lite",
    "api_key_env": "ENCODER_API_KEY",
    "base_url": null,
    "thinking_level": "low",
    "max_output_tokens": 6000,
    "max_retries": 1,
    "requests_per_minute": 5
  }
}
```

This is the `reranker` section inside the complete retrieval JSON. `api_key_env` is
the environment variable's name, not a key value. The selected variable must contain
a key for the selected provider. A missing explicit variable fails; it never silently
uses another provider's credentials. Keys are resolved when the pipeline constructs
the provider, so recreate the pipeline after rotating a key.

For another LLM service with an OpenAI-compatible **Chat Completions** API, copy
[retrieval.compatible-example.json](../configs/retrieval/compatible-example.json)
and replace its placeholder model and endpoint. Choose `provider: openai_compatible`
and the appropriate key environment name. The endpoint is the API base URL, usually
ending in `/v1`; the SDK appends `/chat/completions`. HTTPS is required except for
loopback HTTP servers. This adapter requires the `openai` optional dependency.

Choose `structured_output: json_schema` if the model supports strict JSON Schema;
otherwise choose `json_mode` if it supports JSON-object responses. Plain text-only
models and hosted embedding/reranking endpoints are different API contracts and are
not supported by this adapter. Both modes use the same local ID, grade, quote and
completion validation. There is no automatic provider/model fallback. The selector
uses `llm-relevance-v1` for this adapter and `gemini-relevance-v1` for Gemini; the
provided example files already configure the matching scale. Identical grading
rubrics do not guarantee identical quality across models.

For an older Gemini model that does not support `thinking_level`, set that option to
`null`. A null Gemini `base_url` uses the SDK's normal Google endpoint. Existing
`retrieval.gemini.json` remains backward compatible and uses `Settings.gemini_api_key`
when `api_key_env` is omitted. Local `cross_encoder` continues to require no API key.

Cost controls are explicit: `candidate_limit` controls passages sent together in
**one logical reranking request per query**, `max_output_tokens` caps output,
`max_input_chars` rejects oversized inputs, and `max_retries` limits additional
attempts. `requests_per_minute` controls pacing, not total spend. Reducing candidates
can lose evidence, and a small output cap can cause an explicit incomplete-response
failure. Query embedding calls are separate. No monetary budget or automatic
cheapest-model selection is implemented; response token usage is recorded where the
provider reports it. Compare price and quality before changing the recommended model.

To run the dedicated Gemini configuration:

```powershell
uv run --no-sync trace-impact retrieve runs/saleor-storefront/44dd49dc16a84d2e82153ebd7d65c6ee "What is required to apply a shipping voucher?" --config configs/retrieval/llm.json
```

To evaluate a new choice, copy `configs/evals/gemini-reranking.json`, point its
`retrieval_config` to your new file, and run the existing experiment script. Changing
only the reranker does not require re-ingesting documents or regenerating vectors.
The Gemini and compatible transports share the grading code in
[llm.py](../src/trace_impact/retrieval/rerankers/llm.py); future transports can reuse it or
implement the existing `Reranker` interface.

## Three simple interfaces

| Interface | Input | Output | Built-in implementation |
| --- | --- | --- | --- |
| Retriever | Question, project/run scope and candidate limit | Source passages and original scores | IndexedCorpusRetriever reuses the existing validated search workflow |
| Reranker | Question and candidate passages | A score for every supplied passage ID | IdentityReranker, GeminiReranker through LangChain, or local CrossEncoderReranker |
| EvidenceSelector | Question, candidates and validated ranking | Selected IDs, reason and whether the result cap excluded eligible passages | TopKSelector or ScoreThresholdSelector |

Data lives in [models.py](../src/trace_impact/retrieval/models.py) and stage interfaces in [interfaces.py](../src/trace_impact/retrieval/interfaces.py); orchestration lives in [service.py](../src/trace_impact/retrieval/service.py). Provider-specific requests are isolated in [gemini.py](../src/trace_impact/retrieval/rerankers/gemini.py), while index adaptation is in [adapters.py](../src/trace_impact/retrieval/documents/adapters.py) and selection in [selectors.py](../src/trace_impact/retrieval/selectors.py). JSON models and schema generation live in [config.py](../src/trace_impact/retrieval/config.py).

## What the selected configuration does

The example first retrieves ten passages. Gemini grades each passage against the question using one structured request:

| Grade | Meaning |
| --- | --- |
| 0 | Unrelated |
| 1 | Same topic only |
| 2 | Helpful context without explicit answer evidence |
| 3 | Explicitly supports at least one fact needed to answer or correct the question |

A grade of 3 requires an exact source quote. The service verifies that each ID belongs to the candidates, every candidate has exactly one score, and every supplied quote appears in its passage. Exact quotation establishes provenance, not correctness of the model's relevance judgment.

The selector keeps grade-3 passages, removes exact duplicate text, and returns at most five. Its threshold is tied to `gemini-relevance-v1`; mixing that threshold with the baseline cosine-score scale raises an error. Tied grades keep the original retrieval order. These grades are not probabilities. The threshold is an explicit starting configuration, not a calibrated production decision rule.

No qualifying passage produces `NO_EVIDENCE`. Selected passages produce `EVIDENCE_FOUND`. Both retain `coverage: NOT_EVALUATED`: the pipeline does not claim that every part of a question is answered. `limit_reached` warns when the selector cap excluded otherwise eligible passages. Returning an empty set is not a provider-error fallback: provider failures raise errors.

## Library usage and extension

### Local cross-encoder option

Install the optional CPU dependencies and select the [cross-encoder JSON](../configs/retrieval/cross-encoder.json):

```powershell
uv sync --locked --extra openai --extra gemini --extra vector --extra reranking
uv run --no-sync trace-impact retrieve runs/saleor-storefront/44dd49dc16a84d2e82153ebd7d65c6ee "What is required to apply a shipping voucher?" --config configs/retrieval/cross-encoder.json
```

The pinned [MS MARCO MiniLM L6 model](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2) jointly reads the query and each passage window. This is a dedicated cross-encoder, implemented with Hugging Face Transformers and PyTorch. It makes no external reranking calls. Query embeddings still use the ingestion run's original provider.

The first model load downloads weights into `.model-cache` relative to the working directory. After provisioning, set `local_files_only: true` to forbid model-hub downloads. JSON controls model ID, immutable revision, cache location, batch size, token/window budgets and selection threshold. Credentials are not needed for this public model. Remote model code is disabled and only safetensors weights are loaded. The tested dependency lock uses Transformers 4.57.6, tokenizers 0.22.2 and CPU PyTorch 2.14.1. The optional dependency keeps the core library and Gemini path lightweight.

Each pair has at most 512 tokens, including the query and special tokens. Longer passages use overlapping windows of 64 tokens. All passage tokens are covered, and the adapter checks the expected window count before inference. Query, total-character and total-window limits fail explicitly. The passage receives the maximum raw logit across its windows, preserving its original ID, text and provenance. Max aggregation can favor one relevant subsection; it does not prove complete answer coverage. This adapter does not generate supporting quotes.

The selector's `score_kind` binds the model, revision, token length, overlap and aggregation policy. When changing those fields, update the corresponding score kind using `CrossEncoderOptions(...).score_kind` and reevaluate the threshold. Raw logits are not probabilities and cannot use Gemini's grade-3 threshold. The example uses the predeclared cutoff `0`, which has not been calibrated. It performs worse than Gemini on our development references and remains a local alternative rather than the quality recommendation.

To benchmark another compatible single-logit model, change the JSON model and revision and run:

```powershell
uv run --no-sync python -m trace_impact.evals.runners.retrieval_experiment configs/evals/cross-encoder.json
```

Models must support a fast tokenizer with pair overflow, CPU sequence classification and a declared positional capacity. Other scoring architectures can implement another `Reranker` and register it. The [cross-encoder adapter](../src/trace_impact/retrieval/rerankers/cross_encoder.py) keeps model inference separate from orchestration and selection.

### Python API

```python
from pathlib import Path
from dotenv import load_dotenv
from trace_impact import Settings, create_pipeline

load_dotenv(".env", override=False)
with create_pipeline(Settings.from_env()) as app:
    result = app.retrieve(
        Path("runs/saleor-storefront/44dd49dc16a84d2e82153ebd7d65c6ee"),
        "What is required to apply a shipping voucher?",
    )
    print(result.status, result.selection.ids)
```

For a different retrieval system, implement `Retriever.retrieve(query, scope, limit)` and supply it to `build_retrieval_service(my_retriever, config, app.components)`. That keeps retrieval independent of the ingestion run format. The CLI's retriever remains the indexed-corpus adapter; its embedding and vector-store choices come from the ingestion project configuration.

For a new reranker or selector, implement the relevant protocol, define its options as a Pydantic model, and register a factory with `app.components.rerankers.register_configured_factory(...)` or `app.components.selectors.register_configured_factory(...)`. Supply a `ComponentDefinition` with the options model to get runtime validation and JSON editor definitions. Choose the registered provider name in JSON. The registry caches instances by full configuration and closes their resources when the pipeline context exits.

For direct dependency injection, construct `RetrievalService(my_retriever, my_reranker, my_selector, candidate_limit=10)`. Directly injected components are owned and closed by the caller. The service enforces passage IDs, scope, quotes and selection ordering regardless of provider. Use one pipeline instance per worker; concurrent sharing of mutable provider/registry instances has not been load-tested.

## Failure behavior

The indexed retriever rejects partial indexes and a mismatched run or embedding profile. Retrieved IDs, text and source paths are checked against the corpus. Metadata and original retrieval scores are retained; stage plugins receive copies so their metadata changes cannot alter recorded evidence.

Malformed output, missing or invented IDs, duplicate scores, invalid quotes, nonfinite scores, unknown selection IDs and changed selection order fail explicitly. Empty candidate lists skip the Gemini request. Input size limits fail before requesting the model; evidence is never silently truncated.

The example Gemini configuration spaces requests at five per minute, uses a 45-second per-request timeout, and permits one SDK retry. It rejects incomplete, blocked and refused output. LangSmith tracing is disabled for this call. The provider receives a compatible JSON schema; stricter local length and grade validation remains in effect after parsing. Prompt and documents are treated as data, and no execution tools are bound to the model.

The request pacing and timeout are per process/request; they are not an account-wide quota controller or a distributed deadline. A production deployment still needs its own request admission, authentication, monitoring and independently validated quality thresholds.

## Measure the improvement

```powershell
uv run --no-sync python -m trace_impact.evals.runners.retrieval_experiment configs/evals/gemini-reranking.json
```

This experiment uses the 40 saved candidate lists from the existing vector baseline. It does not repeat embedding or database retrieval. It calls the configured real reranker, then uses the real selector. Pipeline execution receives only the input passages, questions and prior candidate rankings; labels are read in a separate scoring function afterwards.

Each query writes its candidates, scores, source quotes when available, final selection, timing and available usage. To resume an interrupted run, add `--run-dir` with the printed directory. Successful results are reused only when stage configuration, implementation files, dependency lock, runtime package versions and retrieval input fingerprints match. Stage construction time is separate from rerank timing; resumed cached queries report zero new construction time. Failed queries remain errors until retried. A directory lock prevents concurrent writers.

The reports score reranked candidates at cutoffs 1, 2, 3, 5 and 10. They separately score selected sets using the actual number of returned passages, report required-evidence recall, and count empty selections for answerable and unanswerable cases. Fixed-cutoff Precision@5 and variable-result set precision use different denominators; read them together with coverage and result count. The five unanswerable questions measure empty-selection behavior, not final answer accuracy.

See [experiment history](retrieval-experiment-history.md) for completed results. Labels remain draft development references, not held-out or independently approved gold data. Hybrid retrieval and full answer generation are outside this implementation.
