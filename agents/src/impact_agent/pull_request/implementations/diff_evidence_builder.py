"""Build bounded, hash-addressed evidence from the code changed in a pull request."""

import hashlib
from dataclasses import dataclass

from impact_agent.domain.models import Evidence, PullRequestSnapshot


@dataclass(frozen=True, slots=True)
class DiffEvidenceBundle:
    evidence: tuple[Evidence, ...]
    uncovered_files: tuple[str, ...]


class PullRequestDiffEvidenceBuilder:
    """Turn available file patches into direct evidence while respecting a size budget."""

    @staticmethod
    def build(pull_request: PullRequestSnapshot, max_characters: int) -> DiffEvidenceBundle:
        if max_characters < 1:
            raise ValueError("max_characters must be positive")

        if not pull_request.files:
            return DiffEvidenceBundle((), ())

        evidence: list[Evidence] = []
        uncovered_files: list[str] = []
        used_characters = 0
        for changed_file in pull_request.files:
            if not changed_file.patch:
                uncovered_files.append(changed_file.path)
                continue
            content = (
                f"Changed file: {changed_file.path}\n"
                f"Change type: {changed_file.change_type}\n"
                f"Patch:\n{changed_file.patch}"
            )
            if used_characters + len(content) > max_characters:
                uncovered_files.append(changed_file.path)
                continue
            evidence.append(PullRequestDiffEvidenceBuilder._evidence(changed_file.path, content))
            used_characters += len(content)

        return DiffEvidenceBundle(tuple(evidence), tuple(uncovered_files))

    @staticmethod
    def _evidence(path: str, content: str) -> Evidence:
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return Evidence(
            evidence_id=f"pr-diff:{content_hash}",
            source=f"pr-diff://{path}",
            content=content,
            content_sha256=content_hash,
        )
