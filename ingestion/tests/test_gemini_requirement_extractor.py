from types import SimpleNamespace

from google.genai import types

from ingestion.domain.models import Chunk
from ingestion.requirements.implementation.gemini_extractor import (
    GeminiRequirementExtractor,
    _gemini_response_schema,
)


def test_gemini_schema_uses_only_supported_fields():
    schema = _gemini_response_schema()
    serialized = schema.model_dump(by_alias=True, exclude_none=True)

    assert serialized["type"] == types.Type.OBJECT
    assert serialized["required"] == ["requirements"]
    requirement_array = serialized["properties"]["requirements"]
    assert requirement_array["type"] == types.Type.ARRAY
    requirement_item = requirement_array["items"]
    assert requirement_item["required"] == ["statement", "evidence", "source_chunk_id"]
    assert set(requirement_item["properties"]) == {
        "statement",
        "evidence",
        "source_chunk_id",
    }
    assert "additionalProperties" not in str(serialized)
    assert "additional_properties" not in str(serialized)


def test_requirement_extractor_sends_schema_and_validates_returned_json():
    captured: dict[str, object] = {}

    class FakeModels:
        def generate_content(self, **kwargs: object) -> SimpleNamespace:
            captured.update(kwargs)
            return SimpleNamespace(
                parsed={
                    "requirements": [
                        {
                            "statement": "Checkout shows the final total.",
                            "evidence": "shows the final total",
                            "source_chunk_id": "chunk-1",
                        }
                    ]
                },
                text=None,
            )

    extractor = GeminiRequirementExtractor.__new__(GeminiRequirementExtractor)
    extractor._client = SimpleNamespace(models=FakeModels())
    extractor._model = "gemini-3.5-flash-lite"
    extractor._max_chunks = 1
    extractor._batch_size = 1
    extractor._attempts = 1
    extractor._retry_delay = 0
    extractor._minimum_interval = 0
    extractor._last_request = 0

    requirements = extractor.extract(
        (Chunk(id="chunk-1", source_id="doc-1", content="Checkout shows the final total."),)
    )

    assert len(requirements) == 1
    assert requirements[0].source_chunk_id == "chunk-1"
    config = captured["config"]
    assert isinstance(config, types.GenerateContentConfig)
    assert isinstance(config.response_schema, types.Schema)
    assert "additional_properties" not in str(
        config.response_schema.model_dump(by_alias=True, exclude_none=True)
    )
