"""Small CLI around library stages. Commands fail clearly instead of substituting synthetic results."""

import argparse
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from trace_impact import IngestionPipeline, create_pipeline
from trace_impact.shared.errors import IngestionError
from trace_impact.shared.settings import Settings


def main():
    parser = argparse.ArgumentParser(prog="trace-impact")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate-project")
    validate.add_argument("config", type=Path)
    gather = commands.add_parser("collect")
    gather.add_argument("config", type=Path)
    gather.add_argument("--output", type=Path, default=Path("runs"))
    extraction = commands.add_parser("extract")
    extraction.add_argument("run_dir", type=Path)
    extraction.add_argument(
        "--model", default=None, help="Legacy string selections only; JSON model settings take precedence"
    )
    extraction.add_argument("--max-chunks", type=int, default=100)
    commands.add_parser("components")
    schema = commands.add_parser("schema", help="Generate JSON Schema for editor completion")
    schema.add_argument("--output", type=Path, default=Path("schemas/ingestion/project.schema.json"))
    schema.add_argument("--config", type=Path, help="Include this project's custom metadata definitions")
    index = commands.add_parser("index")
    index.add_argument("run_dir", type=Path)
    index.add_argument("--batch-size", type=int, default=16)
    search = commands.add_parser("search")
    search.add_argument("run_dir", type=Path)
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5)
    retrieval = commands.add_parser("retrieve", help="Retrieve, rerank and select evidence")
    retrieval.add_argument("run_dir", type=Path)
    retrieval.add_argument("query")
    retrieval.add_argument("--config", type=Path, help="Optional reranking JSON; default is vector top five")
    retrieval_schema = commands.add_parser(
        "retrieval-schema", help="Generate retrieval JSON editor definitions"
    )
    retrieval_schema.add_argument(
        "--output", type=Path, default=Path("schemas/retrieval/retrieval.schema.json")
    )
    commands.add_parser("doctor")
    commands.add_parser("init-db")
    graph = commands.add_parser("load-graph")
    graph.add_argument("run_dir", type=Path)
    graph.add_argument("--with-requirements", action="store_true")
    code = commands.add_parser("analyze-code", help="Read pinned Git sources; no LLM or database calls")
    code.add_argument("config", type=Path)
    code.add_argument("--output", type=Path, required=True)
    publish_code = commands.add_parser("publish-code-graph")
    publish_code.add_argument("snapshot", type=Path)
    impact = commands.add_parser("impact", help="Scoped Neo4j reverse-dependency retrieval")
    impact.add_argument("graph_id")
    impact.add_argument("query", type=Path)
    code_schema = commands.add_parser("code-schema")
    code_schema.add_argument("--output", type=Path, default=Path("schemas/ingestion/code-graph.schema.json"))
    impact_schema = commands.add_parser("impact-schema")
    impact_schema.add_argument(
        "--output", type=Path, default=Path("schemas/retrieval/impact-query.schema.json")
    )
    args = parser.parse_args()
    load_dotenv(args.env_file, override=False)
    logger = logging.getLogger("trace_impact.events")
    previous_level, previous_propagate = logger.level, logger.propagate
    handler = None
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    try:
        settings = Settings.from_env()
        if getattr(args, "model", None):
            settings = settings.model_copy(update={"ingestion_model": args.model})
        with create_pipeline(settings) as app:
            return dispatch(args, app)
    except IngestionError as exc:
        parser.exit(1, f"{exc.code}: {exc}\n")
    except (ValueError, FileNotFoundError):
        parser.exit(1, "Invalid configuration/input; check source names and local settings.\n")
    except Exception as exc:
        parser.exit(
            1, f"Stage failed ({type(exc).__name__}); check service access and local configuration.\n"
        )
    finally:
        if handler is not None:
            logger.removeHandler(handler)
            handler.close()
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate


def dispatch(args, app: IngestionPipeline):
    if args.command == "validate-project":
        project = app.validate(args.config)
        print(
            json.dumps(
                {
                    "project_id": project.project_id,
                    "sources": len(project.sources),
                    "scope": project.scope,
                    "extractor": project.model_dump()["extractor"],
                    "embedding_provider": project.model_dump()["embedding_provider"],
                },
                indent=2,
            )
        )
    elif args.command == "doctor":
        print(
            json.dumps(
                {
                    name: bool(os.getenv(name))
                    for name in [
                        "NEO4J_URI",
                        "NEO4J_USERNAME",
                        "NEO4J_PASSWORD",
                        "OPENAI_API_KEY",
                        "GEMINI_API_KEY",
                        "INGESTION_MODEL",
                        "EMBEDDING_MODEL",
                        "EMBEDDING_DIMENSIONS",
                        "QDRANT_URL",
                    ]
                },
                indent=2,
            )
        )
    elif args.command == "collect":
        folder, corpus = app.collect(args.config, args.output)
        print(
            json.dumps(
                {
                    "run_dir": str(folder),
                    "sources": len({s.source_id for s in corpus.snapshots}),
                    "documents": len(corpus.snapshots),
                    "chunks": len(corpus.chunks),
                    "errors": corpus.errors,
                },
                indent=2,
            )
        )
        if corpus.errors:
            raise SystemExit(1)
    elif args.command == "extract":
        if args.max_chunks < 1:
            raise ValueError("--max-chunks must be positive")
        run = app.extract(args.run_dir, args.max_chunks)
        print(
            json.dumps(
                {
                    "status": run.status,
                    "requirements": len(run.requirements),
                    "processed_chunks": len(run.processed_chunk_ids),
                    "errors": run.errors,
                },
                indent=2,
            )
        )
        if run.status != "COMPLETE":
            raise SystemExit(1)
    elif args.command == "components":
        print(json.dumps(app.components.describe(), indent=2))
    elif args.command == "schema":
        from trace_impact.ingestion.schema import project_schema
        from trace_impact.ingestion.storage.artifact_store import FileArtifactRepository

        project = app.validate(args.config) if args.config else None
        FileArtifactRepository().write(
            args.output, project_schema(app.components, project.metadata if project else None)
        )
        print(f"Project schema written to {args.output}")
    elif args.command == "index":
        print(app.index(args.run_dir, args.batch_size).model_dump_json(indent=2))
    elif args.command == "search":
        print(
            json.dumps(
                [hit.model_dump() for hit in app.search(args.run_dir, args.query, args.limit)], indent=2
            )
        )
    elif args.command == "retrieve":
        print(
            app.retrieve(args.run_dir, args.query, args.config).model_dump_json(indent=2, ensure_ascii=True)
        )
    elif args.command == "retrieval-schema":
        from trace_impact.ingestion.storage.artifact_store import FileArtifactRepository
        from trace_impact.retrieval.config import retrieval_schema

        FileArtifactRepository().write(args.output, retrieval_schema(app.components))
        print(f"Retrieval schema written to {args.output}")
    elif args.command == "analyze-code":
        from trace_impact.ingestion.storage.artifact_store import FileArtifactRepository

        snapshot = app.analyze_code(args.config)
        FileArtifactRepository().write(args.output, snapshot.model_dump(mode="json"))
        print(
            json.dumps(
                {
                    "graph_id": snapshot.id,
                    "nodes": len(snapshot.nodes),
                    "edges": len(snapshot.edges),
                    "diagnostics": len(snapshot.diagnostics),
                }
            )
        )
    elif args.command == "publish-code-graph":
        from trace_impact.shared.graph_models import GraphSnapshot

        snapshot = GraphSnapshot.model_validate_json(args.snapshot.read_text(encoding="utf-8"))
        print(json.dumps(app.publish_code_graph(snapshot)))
    elif args.command == "impact":
        print(
            app.retrieve_impact(
                args.graph_id, json.loads(args.query.read_text(encoding="utf-8"))
            ).model_dump_json(indent=2, ensure_ascii=True)
        )
    elif args.command == "code-schema":
        from trace_impact.ingestion.code.config import code_schema
        from trace_impact.ingestion.storage.artifact_store import FileArtifactRepository

        FileArtifactRepository().write(args.output, code_schema(app.components))
    elif args.command == "impact-schema":
        from trace_impact.ingestion.storage.artifact_store import FileArtifactRepository
        from trace_impact.shared.graph_models import ImpactQuery

        FileArtifactRepository().write(args.output, ImpactQuery.model_json_schema())
    elif args.command == "load-graph":
        print(json.dumps(app.publish_graph(args.run_dir, args.with_requirements), indent=2))
    else:
        store = app.components.graphs.resolve("neo4j")
        store.initialize()
        print("Neo4j connectivity verified; uniqueness constraints initialized.")


if __name__ == "__main__":
    main()
