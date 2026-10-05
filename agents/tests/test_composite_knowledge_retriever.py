from impact_agent.domain.models import (
    ConfirmedCodeUiMapping,
    Evidence,
    PullRequestRef,
    PullRequestSnapshot,
)
from impact_agent.tools.knowledge.implementations.composite_knowledge_retriever import (
    CompositeKnowledgeRetriever,
)


class FixedRetriever:
    def __init__(self, evidence: tuple[Evidence, ...]) -> None:
        self._evidence = evidence

    def retrieve(self, _pull_request: PullRequestSnapshot) -> tuple[Evidence, ...]:
        return self._evidence


def test_composite_retriever_preserves_confirmed_ui_mapping_within_evidence_limit():
    vector_evidence = Evidence("vector:1", "qdrant://docs", "Doc result", "hash-1")
    graph_evidence = Evidence(
        "neo4j:cart",
        "neo4j://store/cart.ts",
        "Graph result",
        "hash-2",
        confirmed_code_ui_mappings=(
            ConfirmedCodeUiMapping(
                changed_path="src/cart.ts",
                code_path="src/app/cart/page.tsx",
                url="https://store.example/cart",
            ),
        ),
    )
    retriever = CompositeKnowledgeRetriever(
        retrievers=(FixedRetriever((vector_evidence,)), FixedRetriever((graph_evidence,))),
        max_evidence=1,
    )
    pull_request = PullRequestSnapshot(
        PullRequestRef("owner/store", 1), "Update cart", "", "base", "head", (), "diff"
    )

    evidence = retriever.retrieve(pull_request)

    assert evidence == (graph_evidence,)


def test_composite_retriever_balances_vector_and_graph_evidence():
    vector_results = tuple(
        Evidence(f"vector:{index}", "qdrant://docs", "Vector result", f"hash-{index}")
        for index in range(6)
    )
    graph_results = tuple(
        Evidence(f"neo4j:{index}", "neo4j://graph", "Graph relationship", f"graph-{index}")
        for index in range(2)
    )
    retriever = CompositeKnowledgeRetriever(
        retrievers=(FixedRetriever(vector_results), FixedRetriever(graph_results)),
        max_evidence=4,
    )
    pull_request = PullRequestSnapshot(
        PullRequestRef("owner/store", 1), "Update cart", "", "base", "head", (), "diff"
    )

    evidence = retriever.retrieve(pull_request)

    assert [item.evidence_id for item in evidence] == [
        "vector:0",
        "neo4j:0",
        "vector:1",
        "neo4j:1",
    ]
