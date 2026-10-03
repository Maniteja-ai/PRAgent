"""Shared local Git comparison; neither Git authentication nor network access is used."""

import hashlib
import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from trace_coordinator.domain.contracts import as_json_value
from trace_coordinator.domain.errors import ToolFailure
from trace_coordinator.domain.models import ChangeSet, Evidence, FileChange, ToolContext, ToolResult
from trace_coordinator.domain.project import ApplicationConfig
from trace_coordinator.infrastructure.artifacts import save_artifact
from trace_coordinator.tool.implementations.fixture import PRInput


def git(application: ApplicationConfig, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", application.repository_path, *args],
        capture_output=True,
        timeout=application.git_timeout_seconds,
        check=False,
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"},
    )
    if result.returncode:
        raise ToolFailure(
            "Required local Git objects are unavailable; provide a checkout containing the configured commits"
        )
    return result.stdout


@dataclass(frozen=True)
class Comparison:
    base: str
    head: str
    ancestor: str
    patch: bytes
    files: tuple[FileChange, ...]


def compare(application: ApplicationConfig, base: str, head: str) -> Comparison:
    ancestor = git(application, "merge-base", base, head).decode().strip()
    flags = ("diff", "--no-ext-diff", "--no-textconv", "--no-renames")
    patch = git(application, *flags, ancestor, head, "--")
    deployed = git(application, *flags, application.baseline.revision, application.patched.revision, "--")
    if patch != deployed:
        raise ToolFailure(
            "Deployment comparison differs from the change patch; cannot connect this graph to the change"
        )
    records = git(application, *flags, "--name-status", "-z", ancestor, head, "--").decode().split("\0")[:-1]
    statuses = {"A", "D", "M", "T"}
    if any(records[i] not in statuses for i in range(0, len(records), 2)):
        raise ToolFailure("Git returned an unsupported file status")
    files = tuple(
        FileChange(status=cast(Literal["A", "D", "M", "T"], records[i]), path=records[i + 1])
        for i in range(0, len(records), 2)
    )
    if len(files) > application.max_changed_files or len(patch.decode("utf-8")) > application.max_patch_chars:
        raise ToolFailure("Change exceeds configured analysis bounds; no truncated diff was accepted")
    return Comparison(base, head, ancestor, patch, files)


def evidence(
    application: ApplicationConfig,
    root: Path,
    arguments: PRInput,
    context: ToolContext,
    comparison: Comparison,
    *,
    title: str,
    source: str,
    historical_replay: bool,
    provider: str,
    gaps: Sequence[str] = (),
) -> ToolResult:
    if not comparison.files:
        return ToolResult(gaps=(*gaps, "The pinned comparison has no changed files"))
    changes = ChangeSet(
        repository=arguments.repository,
        pull_request=arguments.pull_request,
        upstream_base=comparison.base,
        upstream_head=comparison.head,
        comparison_base=comparison.ancestor,
        analysis_base=application.baseline.revision,
        analysis_head=application.patched.revision,
        files=comparison.files,
        patch_sha256=hashlib.sha256(comparison.patch).hexdigest(),
        historical_replay=historical_replay,
        deployment_patch_equivalent=True,
    )
    saved = save_artifact(root, context.run_id, comparison.patch, ".diff")
    return ToolResult(
        evidence=(
            Evidence(
                id="diff:" + changes.patch_sha256[:20],
                project_id=context.project_id,
                kind="diff",
                source=source,
                summary=title + "\n" + comparison.patch.decode("utf-8"),
                metadata={
                    "changes": changes.model_dump(mode="json"),
                    "artifact": as_json_value(saved),
                    "provider": provider,
                },
            ),
        ),
        gaps=(
            *gaps,
            "Deployed source patch is verified; the current URL's build identity still requires runtime attestation.",
        ),
    )
