import hashlib

from impact_agent.domain.models import ChangedFile, PullRequestRef, PullRequestSnapshot
from impact_agent.pull_request.implementations.diff_evidence_builder import (
    PullRequestDiffEvidenceBuilder,
)


def test_builds_hashed_direct_evidence_with_file_attribution():
    pull_request = PullRequestSnapshot(
        PullRequestRef("owner/storefront", 1),
        "Update checkout",
        "",
        "base",
        "head",
        (ChangedFile("src/checkout.ts", "modified", 2, 1, "+refreshCheckout();"),),
        "diff",
    )

    result = PullRequestDiffEvidenceBuilder.build(pull_request, max_characters=120)

    assert result.uncovered_files == ()
    assert len(result.evidence) == 1
    item = result.evidence[0]
    assert item.source == "pr-diff://src/checkout.ts"
    assert "Changed file: src/checkout.ts" in item.content
    assert "+refreshCheckout();" in item.content
    assert item.evidence_id == f"pr-diff:{hashlib.sha256(item.content.encode()).hexdigest()}"
    assert item.content_sha256 == hashlib.sha256(item.content.encode()).hexdigest()


def test_reports_missing_or_over_budget_file_patches_as_uncovered():
    pull_request = PullRequestSnapshot(
        PullRequestRef("owner/storefront", 1),
        "Update checkout",
        "",
        "base",
        "head",
        (
            ChangedFile("src/large.ts", "modified", 20, 0, "+" + ("x" * 100)),
            ChangedFile("src/no-patch.ts", "modified", 2, 0),
        ),
        "diff",
    )

    result = PullRequestDiffEvidenceBuilder.build(pull_request, max_characters=120)

    assert result.evidence == ()
    assert result.uncovered_files == ("src/large.ts", "src/no-patch.ts")
