"""Offline extraction of explicit normative requirement sentences."""

import hashlib
import re

from ingestion.beans.decorators import component
from ingestion.domain.models import Chunk, Requirement
from ingestion.requirements.interface import RequirementExtractor

SENTENCE_PATTERN = re.compile(r"(?<=[.!?])\s+")
NORMATIVE_PATTERN = re.compile(r"\b(must|should|shall|can|cannot|required|requires)\b", re.I)


@component(contract=RequirementExtractor, name="rule_based")
class RuleBasedRequirementExtractor:
    def extract(self, chunks: tuple[Chunk, ...]) -> tuple[Requirement, ...]:
        requirements: list[Requirement] = []
        for chunk in chunks:
            for sentence in SENTENCE_PATTERN.split(chunk.content):
                statement = " ".join(sentence.split()).strip()
                if len(statement) < 20 or not NORMATIVE_PATTERN.search(statement):
                    continue
                identifier = hashlib.sha256(f"{chunk.id}:{statement}".encode()).hexdigest()
                requirements.append(
                    Requirement(
                        id=identifier,
                        statement=statement,
                        source_chunk_id=chunk.id,
                        evidence=statement,
                    )
                )
        return tuple(requirements)

    def close(self) -> None:
        return None
