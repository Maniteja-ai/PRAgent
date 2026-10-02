"""Small CLI around library stages. Commands fail clearly instead of substituting synthetic results."""

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from .extraction import OpenAIExtractor
from .graph import Neo4jStore
from .models import Corpus, ExtractionRun, load_project
from .pipeline import collect, extract


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
    extraction.add_argument("--model", default=None)
    extraction.add_argument("--max-chunks", type=int, default=100)
    commands.add_parser("doctor")
    commands.add_parser("init-db")
    graph = commands.add_parser("load-graph")
    graph.add_argument("run_dir", type=Path)
    graph.add_argument("--with-requirements", action="store_true")
    args = parser.parse_args()
    load_dotenv(args.env_file, override=False)
    try:
        if args.command == "validate-project":
            project = load_project(args.config)
            print(
                json.dumps(
                    {
                        "project_id": project.project_id,
                        "sources": len(project.sources),
                        "scope": project.scope,
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
                            "INGESTION_MODEL",
                        ]
                    },
                    indent=2,
                )
            )
        elif args.command == "collect":
            folder, corpus = collect(args.config, args.output)
            print(
                json.dumps(
                    {
                        "run_dir": str(folder),
                        "sources": len(corpus.snapshots),
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
            adapter = OpenAIExtractor(
                args.model or os.getenv("INGESTION_MODEL", ""), os.getenv("OPENAI_API_KEY", "")
            )
            run = extract(args.run_dir, adapter, args.max_chunks)
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
        else:
            store = Neo4jStore.from_env()
            try:
                store.initialize()
                if args.command == "load-graph":
                    corpus = Corpus.model_validate_json((args.run_dir / "corpus.json").read_text("utf-8"))
                    requirements = None
                    if args.with_requirements:
                        requirements = ExtractionRun.model_validate_json(
                            (args.run_dir / "extraction.json").read_text("utf-8")
                        )
                    store.load(corpus, requirements)
                    print(json.dumps(store.counts(corpus.project.project_id), indent=2))
                else:
                    print("Neo4j connectivity verified; uniqueness constraints initialized.")
            finally:
                store.close()
    except (ValueError, FileNotFoundError) as exc:
        parser.exit(1, f"Configuration/input error: {exc}\n")
    except Exception as exc:
        # Avoid echoing provider/driver details that might contain credentials or document content.
        parser.exit(
            1, f"Stage failed ({type(exc).__name__}); check service access and local configuration.\n"
        )


if __name__ == "__main__":
    main()
