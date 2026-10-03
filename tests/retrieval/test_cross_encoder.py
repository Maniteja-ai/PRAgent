"""Window coverage, bounded work, score isolation and real offline model execution."""

import sys
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tests.retrieval.test_retrieval import passage
from trace_impact.retrieval.config import CrossEncoderOptions
from trace_impact.retrieval.rerankers.cross_encoder import CrossEncoderReranker, TransformersBackend
from trace_impact.shared.errors import ConfigurationError, ProviderError


def options(**updates):
    return CrossEncoderOptions.model_validate(
        {
            "model": "cross-encoder/test",
            "revision": "a" * 40,
            "max_length": 64,
            "overlap_tokens": 8,
            "max_query_tokens": 10,
            **updates,
        }
    )


class Backend:
    special_tokens = 3

    def __init__(self):
        self.batches, self.closed = [], 0

    def encode(self, text):
        return list(range(len(text)))

    def windows(self, query, passage):
        tokens = self.encode(passage)
        width = 64 - len(query) - 3
        windows, start = [], 0
        while True:
            windows.append({"input_ids": tokens[start : start + width]})
            if start + width >= len(tokens):
                return windows
            start += width - 8

    def score(self, pairs):
        self.batches.append(pairs)
        return [float(max(pair["input_ids"])) for pair in pairs]

    def close(self):
        self.closed += 1


def test_windows_cover_tail_and_preserve_candidate_identity():
    backend = Backend()
    ranker = CrossEncoderReranker(options(batch_size=2), backend)
    result = ranker.rerank("query", (passage("long", text="x" * 150), passage("short", text="abc")))
    windows = [pair["input_ids"] for batch in backend.batches for pair in batch]
    assert set(t for w in windows[:-1] for t in w) == set(range(150))
    assert windows[0][-8:] == windows[1][:8]
    assert all(len(w) + 5 + 3 <= 64 for w in windows)
    assert all(len(b) <= 2 for b in backend.batches)
    assert [(s.id, s.score, s.quote) for s in result.scores] == [("long", 149.0, ""), ("short", 2.0, "")]
    assert result.usage == {"passages": 2, "windows": 4}
    assert not ranker.rerank("query", ()).scores
    ranker.close()
    ranker.close()
    assert backend.closed == 1
    with pytest.raises(ProviderError, match="closed"):
        ranker.rerank("query", ())


@pytest.mark.parametrize(
    "updates,query,text,message",
    [
        ({"max_windows": 1}, "q", "x" * 150, "window budget"),
        ({}, "q" * 11, "x", "query token"),
        ({"overlap_tokens": 60}, "query", "x", "overlap"),
        ({"max_input_chars": 1000}, "q", "x" * 1000, "character"),
        ({}, "", "x", "nonempty"),
        ({}, "q", " ", "nonempty"),
    ],
)
def test_budgets_fail_before_inference(updates, query, text, message):
    backend = Backend()
    with pytest.raises(ValueError, match=message):
        CrossEncoderReranker(options(**updates), backend).rerank(query, (passage(text=text),))
    assert not backend.batches


@pytest.mark.parametrize("values", [[], [float("nan")], [float("inf")], [1, 2]])
def test_bad_backend_scores_fail(values):
    backend = Backend()
    backend.score = lambda pairs: values
    with pytest.raises(ProviderError, match="invalid scores"):
        CrossEncoderReranker(options(), backend).rerank("q", (passage(),))


def test_backend_crash_is_not_empty_success():
    backend = Backend()

    def fail(pairs):
        raise RuntimeError("private input")

    backend.score = fail
    with pytest.raises(ProviderError, match="inference failed") as error:
        CrossEncoderReranker(options(), backend).rerank("q", (passage(),))
    assert "private" not in str(error.value)


def test_missing_overflow_window_fails_before_inference():
    backend = Backend()
    backend.windows = lambda query, text: [{"input_ids": [1]}]
    with pytest.raises(ProviderError, match="preserve all"):
        CrossEncoderReranker(options(), backend).rerank("q", (passage(text="x" * 150),))
    assert not backend.batches


def test_duplicates_and_empty_tokenization():
    backend = Backend()
    ranker = CrossEncoderReranker(options(), backend)
    with pytest.raises(ValueError, match="unique"):
        ranker.rerank("q", (passage(), passage()))
    backend.encode = lambda text: [1] if text == "q" else []
    with pytest.raises(ValueError, match="no tokens"):
        ranker.rerank("q", (passage(),))


def test_score_scale_binds_revision_and_window_policy():
    original = options().score_kind
    for changes in ({"revision": "b" * 40}, {"max_length": 128}, {"overlap_tokens": 0}, {"model": "other"}):
        assert options(**changes).score_kind != original
    with pytest.raises(ValidationError):
        options(revision="main")


def test_optional_dependency_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)
    with pytest.raises(ConfigurationError, match="optional dependency"):
        TransformersBackend(options())


def test_real_transformers_backend_without_network(tmp_path, monkeypatch):
    torch = pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")
    from transformers import BertConfig, BertForSequenceClassification, BertTokenizerFast

    vocab = tmp_path / "vocab.txt"
    vocab.write_text("[PAD]\n[UNK]\n[CLS]\n[SEP]\n[MASK]\nquery\nshipping\nvoucher\n", encoding="utf-8")
    tokenizer = BertTokenizerFast(vocab_file=str(vocab), model_max_length=64)
    model = BertForSequenceClassification(
        BertConfig(
            vocab_size=len(tokenizer),
            hidden_size=8,
            num_hidden_layers=1,
            num_attention_heads=2,
            intermediate_size=16,
            num_labels=1,
            max_position_embeddings=64,
        )
    )
    calls = []

    def load_tokenizer(name, **kwargs):
        calls.append(kwargs)
        return tokenizer

    def load_model(name, **kwargs):
        calls.append(kwargs)
        return model

    monkeypatch.setattr(transformers.AutoTokenizer, "from_pretrained", load_tokenizer)
    monkeypatch.setattr(transformers.AutoModelForSequenceClassification, "from_pretrained", load_model)
    backend = TransformersBackend(options(local_files_only=True))
    assert all(
        c["revision"] == "a" * 40 and c["trust_remote_code"] is False and c["token"] is False for c in calls
    )
    assert calls[1]["use_safetensors"] is True and calls[0]["local_files_only"] is True
    assert not model.training and all(not p.requires_grad for p in model.parameters())
    pairs = backend.windows("query", "shipping voucher")
    scores = backend.score(pairs)
    features = tokenizer("query", "shipping voucher")
    with torch.inference_mode():
        expected = model(**tokenizer.pad([features], return_tensors="pt")).logits[0, 0].item()
    assert scores == pytest.approx([expected])
    with pytest.raises(ProviderError, match="capacity"):
        backend.score([{"input_ids": [1] * 65}])
    long_text = "shipping " * 200 + "voucher"
    windows = backend.windows("query", long_text)
    # Reassemble the passage spans after stripping query and model special tokens.
    recovered = windows[0]["input_ids"][3:-1]
    for window in windows[1:]:
        recovered += window["input_ids"][3 + 8 : -1]
    assert recovered == backend.encode(long_text)
    backend.model = lambda **kwargs: SimpleNamespace(logits=torch.zeros((1, 2)))
    with pytest.raises(ProviderError, match="shape"):
        backend.score(pairs)
    backend.close()
    assert backend.model is None
    model.config.num_labels = 2
    with pytest.raises(ConfigurationError, match="single-logit"):
        TransformersBackend(options())
    model.config.num_labels = 1
    with pytest.raises(ConfigurationError, match="capacity"):
        TransformersBackend(options(max_length=128))

    def fail(*args, **kwargs):
        raise OSError("private path")

    monkeypatch.setattr(transformers.AutoTokenizer, "from_pretrained", fail)
    with pytest.raises(ProviderError, match="load failed"):
        TransformersBackend(options())
