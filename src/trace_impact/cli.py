"""Small CLI around library stages. Commands fail clearly instead of substituting synthetic results."""

import argparse
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from . import IngestionPipeline, create_pipeline
from .config import Settings
from .errors import IngestionError


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
    index = commands.add_parser("index")
    index.add_argument("run_dir", type=Path)
    index.add_argument("--batch-size", type=int, default=16)
    search = commands.add_parser("search")
    search.add_argument("run_dir", type=Path)
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5)
    commands.add_parser("doctor")
    commands.add_parser("init-db")
    graph = commands.add_parser("load-graph")
    graph.add_argument("run_dir", type=Path)
    graph.add_argument("--with-requirements", action="store_true")
    args = parser.parse_args()
    load_dotenv(args.env_file, override=False)
    logger = logging.getLogger("trace_impact.events")
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
    elif args.command == "index":
        print(app.index(args.run_dir, args.batch_size).model_dump_json(indent=2))
    elif args.command == "search":
        print(
            json.dumps(
                [hit.model_dump() for hit in app.search(args.run_dir, args.query, args.limit)], indent=2
            )
        )
    elif args.command == "load-graph":
        print(json.dumps(app.publish_graph(args.run_dir, args.with_requirements), indent=2))
    else:
        store = app.components.graphs.resolve("neo4j")
        store.initialize()
        print("Neo4j connectivity verified; uniqueness constraints initialized.")


if __name__ == "__main__":
    main()
