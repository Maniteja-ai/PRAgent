# Ingestion library: simple low-level design

Status: implemented library structure, version 0.3. Live extraction and Neo4j verification still require credentials. Read this before the larger future-platform proposal.

## The idea

The user chooses a name in configuration. A registry finds the matching class. The pipeline calls that class through a small interface. To add a new implementation, write a class, register it, and select its name. The pipeline stays unchanged when the new behavior fits an existing contract.

For example: `loader: github_file` fetches README bytes. `parser: markdown` converts those bytes to a document. Tomorrow `loader: internal_api` can fetch bytes from a different system and reuse that Markdown parser.

A loader answers **where does the content come from?** A parser answers **how do we read this format?** A requirement extractor answers **what behavior does the text describe?** These are separate choices.

## Flow

```text
User project configuration (JSON or YAML)
                  |
          Named component registries
                  |
Loader -> RawDocument -> Parser -> Document -> Chunker -> Chunks
                  |                       |                |
                  +--- local artifacts ---+                |
                                              +-----------+-----------+
                                              |                       |
                                      RequirementExtractor     EmbeddingProvider
                                              |                       |
                                       Evidence validation       VectorStore
                                              |                    Qdrant
                                          GraphStore                 |
                                            Neo4j              Search evidence
```

The graph and vector branches run as separate explicit commands. Extraction does not require vector indexing; search does not require extraction. Their shared chunk IDs connect the evidence. A later answer-generation stage can use retrieved chunks and graph relationships.

## Interfaces and current implementations

| Interface | Responsibility | Registered names |
|---|---|---|
| `SourceLoader` | Fetch one or more raw documents | `web`, `github_file`, `local_file` |
| `DocumentParser` | Convert bytes to normalized text | `html`, `markdown` |
| `Chunker` | Split text while retaining section/source identity | `section` |
| `RequirementExtractor` | Propose structured requirements with exact source quotes | `langchain` |
| `EmbeddingProvider` | Embed document text and queries with the same profile | `openai` |
| `GraphStore` | Publish requirements, provenance and relationships | `neo4j` |
| `VectorStore` | Store, verify and search vectors within one project/run/profile | `qdrant` |
| `ArtifactStore` | Save files, manifests, caches and checkpoints | `local` |

The interfaces use Python `Protocol`: implement the named methods and fields; a shared base class is unnecessary. `interfaces.py` contains the complete signatures. Configuration selects trusted registered names, never arbitrary Python import paths.

## Public objects

- `Settings`: runtime credentials, model names, dimensions and endpoints. Secrets stay outside project files.
- `Components`: one registry for each interface above.
- `IngestionPipeline`: coordinates `validate`, `collect`, `extract`, `publish_graph`, `index`, and `search`.
- `create_pipeline(settings)`: constructs the standard registries. Providers are created lazily when used.
- Models: `RawDocument`, `Document`, `Chunk`, `Snapshot`, `Corpus`, `ExtractionRun`, `EmbeddingProfile`, `VectorIndexRun`, and `SearchHit`.

`Source` carries `loader`, `parser`, `location`, `options`, `authority`, `version`, and `scope`. Loader-specific settings go in `options`; each implementation validates its own options. A loader that returns multiple documents must assign a stable, unique `RawDocument.key` to each document.

Use the pipeline as a context manager. It closes created HTTP clients and database clients. A plugin can expose `close()` when it owns a resource. Share a resource through one owner; different registries do not coordinate ownership of the same object.

## User configuration

A complete runnable example is in `projects/plugin-example/project.yaml`. The relevant selection is:

```yaml
sources:
  - id: features
    loader: local_file
    location: features.json
    parser: json_features
    authority: frontend_spec
    version: synthetic-v1
    scope: [catalog search, loan renewal]
chunker: section
extractor: langchain
embedding_provider: openai
storage:
  graph: neo4j
  vector: qdrant
  artifacts: local
```

The rest of the project file identifies the application, repository revision, baseline URL and scope. Saleor uses the same schema with nine sources. A README is a Markdown document, not a special extraction method: choose its loader according to its location.

## Add a new implementation

The working example `examples/custom_parser.py` adds a JSON requirements parser:

```python
from pathlib import Path
from trace_impact import create_pipeline
from examples.custom_parser import JsonFeatureParser

with create_pipeline() as pipeline:
    pipeline.components.parsers.register("json_features", JsonFeatureParser())
    run_dir, corpus = pipeline.collect(Path("projects/plugin-example/project.yaml"))
```

The project selects `parser: json_features`. Collection then uses the existing file loader and chunker with your new parser.

The same process applies elsewhere:

| New need | Implement | Register in | Config selection |
|---|---|---|---|
| Read from an internal API | `SourceLoader.load(...)` | `components.loaders` | source `loader` |
| Read PDF bytes | `DocumentParser.parse(...)` | `components.parsers` | source `parser` |
| Different chunking algorithm | `Chunker.split(...)` | `components.chunkers` | `chunker` |
| Different requirement extraction | `RequirementExtractor.extract(...)` plus provenance fields | `components.extractors` | `extractor` |
| Another embedding model | `EmbeddingProvider` | `components.embeddings` | `embedding_provider` |
| Another vector database | `VectorStore` | `components.vectors` | `storage.vector` |

These are extension examples, not already supplied plugins. SDK registrations live in your Python entry point. The stock CLI only knows built-ins. To expose a custom implementation through a separate CLI, use your registration code in that CLI's composition function.

A genuinely new stage, such as browser exploration, needs its own contract and orchestration. Existing interfaces should not be stretched to represent every future feature.

## What goes where

**Neo4j:** project/run/source/snapshot identities, small chunk references, structured requirement candidates, citation links and coverage assessments. A `ChunkRef` stores a heading, text hash and artifact path, not the full chunk body. Short citation quotes remain on relationships. Initially coverage is `NOT_EVALUATED`.

**Qdrant:** chunk text, embeddings, source/chunk IDs, artifact references and project/run/profile filters. Search uses the same embedding model and dimensions as indexing. A model/profile change creates a separate collection.

**Local artifacts:** original bytes, normalized documents, complete corpus manifest, extraction output, review queue and caches. Files are the reproducible evidence behind both databases.

Code dependency relationships and observed UI relationships belong in the future Neo4j expansion. This release does not extract those relationships. There is no claim that static analysis can recover every dynamic dependency.

## Reliability rules

1. Validate registered names before collection. Credentials are needed only for the stage that uses them.
2. Hash content, source configuration and processor versions into snapshot identities. Preserve original bytes and normalized text.
3. Restrict built-in web fetching to configured HTTPS hosts, including redirects, and bounded response sizes. GitHub files require an exact commit. Local files stay inside the config directory.
4. Use local run locks and atomic file replacement. A partial collection cannot proceed to extraction or indexing.
5. Cache extraction by extractor fingerprint, project configuration and chunk ID. Re-running extraction reuses successful results; increase `max_chunks` to process beyond an earlier cap.
6. Validate exact evidence quotes independently of the model. A matching quote is still a candidate, not proof of semantic correctness. Backend contracts do not automatically become frontend guarantees.
7. Cache embeddings by text and model profile. Upsert deterministic vector IDs, read back each batch, and mark the index complete only after all batches verify. Retrying reuses cached embeddings and repeats idempotent writes.
8. Search requires a complete matching index and filters by project, run and profile. Returned evidence is checked against the selected corpus.
9. Neo4j refuses partial or mismatched extraction runs. Its transaction publishes the graph; Qdrant publication is separate. There is no distributed transaction across the two databases.

Current storage plugins use filesystem paths. `corpus.json` remains the local entry manifest even when selecting a different artifact implementation. A remote-only object store needs a manifest/bootstrap change, so it is not currently a drop-in replacement.

## Patterns, without extra layers

- **Strategy:** each stage uses an interface whose behavior can be replaced.
- **Registry and factory:** names select instances; lazy factories delay expensive setup.
- **Adapter:** LangChain, Neo4j and Qdrant APIs stay inside their implementations.
- **Dependency injection:** the pipeline receives components; it does not choose vendors internally.

LangChain is used for structured model output and embeddings. Ordinary source loading, file handling and deterministic validation remain small Python components.

## Read the code in this order

```text
examples/custom_parser.py     A complete extension you can run
projects/plugin-example/     User inputs for that extension
src/trace_impact/models.py    Data passed between stages
src/trace_impact/interfaces.py  What each implementation must provide
src/trace_impact/registry.py  How names select classes
src/trace_impact/pipeline.py  How the stages connect
src/trace_impact/bootstrap.py  Built-in registrations
src/trace_impact/implementations/  Concrete implementations
```

`_workflows.py` and `_indexing.py` contain private orchestration, checkpoints and recovery. They are separated to keep the public pipeline readable.

## Assignment boundary

Implemented here: reusable document ingestion, extraction adapter and validation, graph publication adapter, embedding/indexing adapter and dense retrieval. Tests exercise the real local Qdrant store using explicitly labeled test embeddings; those are not production semantic results.

Pending live credentials: real model extraction/embedding and Neo4j round-trip verification. Pending assignment stages: autonomous browser crawl, code analysis, evidence-backed UI/code/requirement mapping, PR blast-radius reporting, full RAG answer generation and evaluation. The broader proposed platform document is a future review draft, not an implementation status report.
