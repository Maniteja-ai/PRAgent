import argparse
from pathlib import Path

from ingestion.bootstrap import create_code_index, create_code_ui_refresh, create_pipeline
from ingestion.config_loader.implementation.json_config_loader import JsonConfigLoader


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the ingestion pipeline")
    parser.add_argument("config", type=Path, help="Path to the ingestion configuration JSON")
    parser.add_argument(
        "--code-index-only",
        action="store_true",
        help="Refresh source-code embeddings and Neo4j dependencies without reingesting documents",
    )
    parser.add_argument(
        "--code-ui-only",
        action="store_true",
        help="Refresh code relationships and browser mappings without model or vector calls",
    )
    parser.add_argument(
        "--resume-after-embedding",
        action="store_true",
        help="Continue from saved document, chunk, and vector artifacts without re-embedding",
    )
    arguments = parser.parse_args()
    config = JsonConfigLoader().load(arguments.config)
    selected_modes = sum(
        (arguments.code_index_only, arguments.code_ui_only, arguments.resume_after_embedding)
    )
    if selected_modes > 1:
        parser.error("--code-index-only, --code-ui-only, and --resume-after-embedding are exclusive")
    if arguments.code_index_only:
        with create_code_index(config) as pipeline:
            print(pipeline.run().model_dump_json(indent=2))
        return
    if arguments.code_ui_only:
        with create_code_ui_refresh(config) as pipeline:
            print(pipeline.run().model_dump_json(indent=2))
        return
    with create_pipeline(config) as pipeline:
        result = (
            pipeline.resume_after_embedding()
            if arguments.resume_after_embedding
            else pipeline.run()
        )
        print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
