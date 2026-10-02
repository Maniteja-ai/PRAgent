"""Run: uv run python examples/custom_parser.py

This real parser plugin ingests a synthetic JSON specification. It does not call an LLM.
"""

import json
from pathlib import Path

from trace_impact import create_pipeline
from trace_impact.models import Document, RawDocument


class JsonFeatureParser:
    version = "json-features-v1"

    def parse(self, raw: RawDocument) -> Document:
        data = json.loads(raw.content)
        features = data.get("features") if isinstance(data, dict) else None
        if (
            not isinstance(features, list)
            or not features
            or not all(isinstance(x, str) and x.strip() for x in features)
        ):
            raise ValueError("Expected a nonempty features array of strings")
        return Document(text="# Product requirements\n\n" + "\n\n".join(features))


def main():
    root = Path(__file__).resolve().parents[1]
    with create_pipeline() as pipeline:
        pipeline.components.parsers.register("json_features", JsonFeatureParser())
        run_dir, corpus = pipeline.collect(root / "projects/plugin-example/project.yaml", root / "runs")
        if corpus.errors:
            raise RuntimeError("Plugin example failed to collect its source")
        print(f"Custom parser completed: {len(corpus.chunks)} chunks in {run_dir}")


if __name__ == "__main__":
    main()
