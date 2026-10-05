from pathlib import Path
from types import SimpleNamespace

from ingestion.config_loader.implementation.json_config_loader import JsonConfigLoader
from ingestion.embedding.implementation import gemini_embedding
from ingestion.embedding.implementation.gemini_embedding import GeminiEmbeddingProvider


class FakeModels:
    def __init__(self):
        self.calls = []

    def embed_content(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            embeddings=[SimpleNamespace(values=[0.1, 0.2, 0.3]) for _ in kwargs["contents"]]
        )


class FakeClient:
    def __init__(self):
        self.models = FakeModels()
        self.closed = False

    def close(self):
        self.closed = True


def test_gemini_embedding_2_uses_document_prefix_without_legacy_task_type(monkeypatch):
    config = JsonConfigLoader().load(Path(__file__).parents[1] / "configs" / "saleor.json")
    fake_client = FakeClient()
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(gemini_embedding.genai, "Client", lambda **_kwargs: fake_client)

    provider = GeminiEmbeddingProvider(config)
    vectors = provider.embed(("Saleor checkout voucher documentation",))

    request = fake_client.models.calls[0]
    assert request["model"] == "gemini-embedding-2"
    assert request["contents"][0].parts[0].text.startswith("title: none | text: ")
    assert request["config"].task_type is None
    assert vectors == ((0.1, 0.2, 0.3),)
    provider.close()
    assert fake_client.closed
