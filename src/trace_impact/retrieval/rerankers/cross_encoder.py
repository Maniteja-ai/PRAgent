"""Local query-passage cross-encoding; bounded windows preserve every passage token.

The backend owns tokenization and model inference. The adapter owns windowing and
ID mapping, so it can be tested without downloading weights or importing Torch.
"""

import math
from typing import Protocol

from trace_impact.retrieval.config import CrossEncoderOptions
from trace_impact.retrieval.models import PassageScore, Ranking
from trace_impact.shared.errors import ConfigurationError, ProviderError


class CrossEncoderBackend(Protocol):
    special_tokens: int

    def encode(self, text: str) -> list[int]: ...
    def windows(self, query: str, passage: str) -> list[dict[str, list[int]]]: ...
    def score(self, pairs: list[dict[str, list[int]]]) -> list[float]: ...
    def close(self) -> None: ...


class CrossEncoderReranker:
    def __init__(self, options: CrossEncoderOptions, backend: CrossEncoderBackend):
        self.options, self.backend = options, backend
        self.closed = False

    def rerank(self, query, candidates):
        if self.closed:
            raise ProviderError("Cross-encoder is closed")
        if not candidates:
            return Ranking(score_kind=self.options.score_kind, scores=())
        if not query.strip() or any(not p.text.strip() for p in candidates):
            raise ValueError("Cross-encoder requires nonempty query and passages")
        if len({p.id for p in candidates}) != len(candidates):
            raise ValueError("Cross-encoder candidate IDs must be unique")
        if len(query) + sum(len(p.text) for p in candidates) > self.options.max_input_chars:
            raise ValueError("Cross-encoder input character budget exceeded")
        try:
            query_tokens = self.backend.encode(query)
            if not query_tokens or len(query_tokens) > self.options.max_query_tokens:
                raise ValueError("Cross-encoder query token budget exceeded")
            width = self.options.max_length - len(query_tokens) - self.backend.special_tokens
            if width <= self.options.overlap_tokens:
                raise ValueError("Cross-encoder window must be larger than its overlap")
            pairs, owners = [], []
            # Complete planning before inference: a budget failure never returns partial evidence.
            for i, passage in enumerate(candidates):
                tokens = self.backend.encode(passage.text)
                if not tokens:
                    raise ValueError("Cross-encoder passage has no tokens")
                count = 1 + max(0, math.ceil((len(tokens) - width) / (width - self.options.overlap_tokens)))
                if len(pairs) + count > self.options.max_windows:
                    raise ValueError("Cross-encoder window budget exceeded")
                windows = self.backend.windows(query, passage.text)
                if len(windows) != count:
                    raise ProviderError("Cross-encoder did not preserve all passage windows")
                pairs.extend(windows)
                owners.extend([i] * count)
            scores = [float("-inf")] * len(candidates)
            for start in range(0, len(pairs), self.options.batch_size):
                batch = pairs[start : start + self.options.batch_size]
                values = self.backend.score(batch)
                if len(values) != len(batch) or any(not math.isfinite(v) for v in values):
                    raise ProviderError("Cross-encoder returned invalid scores")
                for i, value in enumerate(values, start):
                    scores[owners[i]] = max(scores[owners[i]], value)
            return Ranking(
                score_kind=self.options.score_kind,
                scores=tuple(PassageScore(id=p.id, score=s) for p, s in zip(candidates, scores, strict=True)),
                usage={"passages": len(candidates), "windows": len(pairs)},
            )
        except (ValueError, ProviderError):
            raise
        except Exception:
            raise ProviderError("Cross-encoder inference failed; no evidence was selected") from None

    def close(self):
        if not self.closed:
            self.closed = True
            self.backend.close()


class TransformersBackend:
    """CPU-only single-logit inference; no decoding, truncation, or remote model code."""

    def __init__(self, options: CrossEncoderOptions):
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError:
            raise ConfigurationError(
                "Install the 'reranking' optional dependency to use cross_encoder"
            ) from None
        self.torch, self.options = torch, options
        common = {
            "revision": options.revision,
            "cache_dir": options.cache_directory,
            "local_files_only": options.local_files_only,
            "trust_remote_code": False,
            "token": False,
        }
        try:
            self.tokenizer = AutoTokenizer.from_pretrained(options.model, use_fast=True, **common)
            self.model = AutoModelForSequenceClassification.from_pretrained(
                options.model, use_safetensors=True, **common
            ).to("cpu")
            self.model.eval()
            self.model.requires_grad_(False)
        except Exception:
            raise ProviderError(
                "Cross-encoder model load failed; check revision, cache and connectivity"
            ) from None
        if self.model.config.num_labels != 1:
            raise ConfigurationError("Cross-encoder requires a single-logit relevance model")
        if not self.tokenizer.is_fast:
            raise ConfigurationError("Cross-encoder requires a fast tokenizer with overflow support")
        supported = min(self.tokenizer.model_max_length, self.model.config.max_position_embeddings)
        if options.max_length > supported:
            raise ConfigurationError("Configured cross-encoder length exceeds model capacity")
        self.special_tokens = self.tokenizer.num_special_tokens_to_add(pair=True)

    def encode(self, text):
        return self.tokenizer.encode(text, add_special_tokens=False, truncation=False, verbose=False)

    def windows(self, query, passage):
        # The fast tokenizer returns every overflow window, including the final tail.
        encoded = self.tokenizer(
            query,
            passage,
            truncation="only_second",
            max_length=self.options.max_length,
            stride=self.options.overlap_tokens,
            return_overflowing_tokens=True,
            padding=False,
        )
        return [
            {key: encoded[key][i] for key in self.tokenizer.model_input_names if key in encoded}
            for i in range(len(encoded["input_ids"]))
        ]

    def score(self, pairs):
        if any(len(item["input_ids"]) > self.options.max_length for item in pairs):
            raise ProviderError("Cross-encoder pair exceeds configured token capacity")
        batch = self.tokenizer.pad(pairs, padding=True, return_tensors="pt")
        with self.torch.inference_mode():
            logits = self.model(**batch).logits
        if tuple(logits.shape) != (len(pairs), 1):
            raise ProviderError("Cross-encoder returned an unexpected score shape")
        # Raw logits are deliberately not represented as calibrated probabilities.
        return logits[:, 0].tolist()

    def close(self):
        self.model = None
        self.tokenizer = None


def build_cross_encoder(options: CrossEncoderOptions) -> CrossEncoderReranker:
    return CrossEncoderReranker(options, TransformersBackend(options))
