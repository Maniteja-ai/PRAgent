"""Named values used by the analysis graph and its audit trail."""

from enum import StrEnum


class PipelineNode(StrEnum):
    """Stable LangGraph node identifiers."""

    VALIDATE_PR_REQUEST = "validate_pr_request"
    FETCH_PULL_REQUEST = "fetch_pull_request"
    RETRIEVE_KNOWLEDGE = "retrieve_knowledge"
    EXPLORE_UI = "explore_ui"
    ANALYZE_IMPACT = "analyze_impact"
    VERIFY_BEHAVIOR = "verify_behavior"
    CREATE_REPORT = "create_report"


class EvaluationStage(StrEnum):
    """Readable names stored in per-stage evaluation history."""

    VALIDATE_PR_REQUEST = "Validate PR request"
    FETCH_PULL_REQUEST = "Fetch pull request"
    RETRIEVE_KNOWLEDGE = "Retrieve knowledge"
    EXPLORE_UI = "Explore UI"
    ANALYZE_IMPACT = "Analyze impact"
    VERIFY_BEHAVIOR = "Verify behavior"
    CREATE_REPORT = "Create report"


class CoverageGap(StrEnum):
    """Honest report notes for analysis stages deliberately left disabled."""

    UI_EXPLORATION_DISABLED = "UI exploration is disabled"
    BEHAVIOR_VERIFICATION_DISABLED = "Behavior verification is disabled"
    KNOWLEDGE_RETRIEVAL_DISABLED = "Knowledge retrieval is disabled"
    VECTOR_RETRIEVAL_DISABLED = "Vector retrieval is disabled"
    CODE_GRAPH_DATA_MISSING = "No indexed code graph data matched the changed files"
    CODE_TO_UI_MAPPING_MISSING = "No confirmed code-to-UI mapping matched the changed files"


class ReportStatus(StrEnum):
    """Final outcome of one coordinator run."""

    COMPLETED = "COMPLETED"
    COMPLETED_WITH_GAPS = "COMPLETED_WITH_GAPS"
    FAILED = "FAILED"
