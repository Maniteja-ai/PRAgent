"""Local change source with explicit commit IDs and no remote PR metadata lookup."""

from trace_coordinator.domain.errors import ToolFailure
from trace_coordinator.domain.project import LocalGitSource
from trace_coordinator.infrastructure.adapters.fixtures import PRInput
from trace_coordinator.infrastructure.adapters.git_changes import compare, evidence, git
from trace_coordinator.infrastructure.ledger import digest


class LocalGitDiffTool:
    # Keep the stable dispatcher identity so provider changes cannot create a second quota.
    name = "github.diff"
    description = "Read the configured local Git commits and changed files. No GitHub API or token is used."
    input_model = PRInput
    allowed_agents = frozenset({"coordinator"})

    def __init__(self, application, artifact_root):
        if not isinstance(application.change_source, LocalGitSource):
            raise ValueError("Local Git adapter requires a local_git change source")
        self.app, self.root = application, artifact_root
        self.version = "local-git-v1:" + digest(application.model_dump(mode="json"))

    def execute(self, arguments, context):
        selected = self.app.change_source
        if (
            arguments.repository != self.app.repository
            or context.project_id != self.app.project_id
            or arguments.pull_request != selected.pull_request
        ):
            raise ToolFailure("Request does not match the configured local change")
        comparison = compare(self.app, selected.base_revision, selected.head_revision)
        subject = (
            git(self.app, "show", "-s", "--format=%s", selected.head_revision, "--")
            .decode("utf-8")
            .strip()[:300]
        )
        return evidence(
            self.app,
            self.root,
            arguments,
            context,
            comparison,
            title="Local commit: " + subject,
            source=f"local-git:{self.app.repository_path}#{comparison.ancestor}..{comparison.head}",
            historical_replay=selected.historical_replay,
            provider="local_git",
            gaps=(
                "PR number and replay status are configuration labels; remote PR metadata was not verified.",
            ),
        )
