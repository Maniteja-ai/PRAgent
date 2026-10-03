import json
import subprocess
from pathlib import Path

import httpx
import pytest

from trace_coordinator.domain.errors import ToolFailure
from trace_coordinator.domain.models import ToolContext
from trace_coordinator.domain.project import (
    ApplicationConfig,
    ApplicationFile,
    GraphConfigFile,
    LocalGitSource,
    PlaywrightUIConfigFile,
    RetrievalConfigFile,
    load_application,
)
from trace_coordinator.storage.artifacts import save_artifact
from trace_coordinator.tool.implementations.fixture import PRInput
from trace_coordinator.tool.implementations.github import GitHubDiffTool
from trace_coordinator.tool.implementations.local_git import LocalGitDiffTool

ROOT = Path(__file__).resolve().parents[1]


def application(tmp_path, base="a" * 40, head="b" * 40):
    return ApplicationConfig(
        project_id="project",
        repository="owner/repo",
        repository_path=str(tmp_path),
        graph_snapshot_file=str(tmp_path / "graph.json"),
        ingestion_run_directory=str(tmp_path),
        retrieval_config_file=str(tmp_path / "retrieval.json"),
        vector_directory=str(tmp_path / "vector"),
        baseline={"url": "https://baseline.example", "revision": base},
        patched={"url": "https://patched.example", "revision": head},
    )


@pytest.fixture
def git_repo(tmp_path):
    def git(*args):
        return (
            subprocess.check_output(["git", "-C", str(tmp_path), *args], stderr=subprocess.DEVNULL)
            .decode()
            .strip()
        )

    git("init")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    (tmp_path / "checkout.ts").write_text("export const total = 10;\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "base")
    base = git("rev-parse", "HEAD")
    (tmp_path / "checkout.ts").write_text("export const total = 9;\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "voucher")
    head = git("rev-parse", "HEAD")
    return application(tmp_path, base, head)


def client_for(app, *, changed_head=False):
    count = 0

    def handle(request):
        nonlocal count
        count += 1
        return httpx.Response(
            200,
            json={
                "number": 1,
                "title": "Voucher change",
                "merged": True,
                "base": {"sha": app.baseline.revision, "repo": {"full_name": app.repository}},
                "head": {"sha": "c" * 40 if changed_head and count > 1 else app.patched.revision},
                "html_url": "https://github.com/owner/repo/pull/1",
            },
        )

    return httpx.Client(transport=httpx.MockTransport(handle))


def context():
    return ToolContext(run_id="test", project_id="project", agent_id="coordinator")


def test_pinned_git_diff_and_deployment_equivalence(git_repo, tmp_path):
    with client_for(git_repo) as client:
        tool = GitHubDiffTool(git_repo, tmp_path / "artifacts", client=client)
        result = tool.execute(PRInput(repository="owner/repo", pull_request=1), context())
    changes = result.evidence[0].metadata["changes"]
    assert changes["deployment_patch_equivalent"]
    assert changes["historical_replay"]
    assert changes["files"] == [{"path": "checkout.ts", "status": "M"}]
    artifact = result.evidence[0].metadata["artifact"]
    assert Path(artifact["path"]).is_file()
    assert "-export const total = 10;" in result.evidence[0].summary


def test_force_push_during_read_is_rejected(git_repo, tmp_path):
    with client_for(git_repo, changed_head=True) as client:
        tool = GitHubDiffTool(git_repo, tmp_path / "artifacts", client=client)
        with pytest.raises(ToolFailure, match="changed during"):
            tool.execute(PRInput(repository="owner/repo", pull_request=1), context())


def test_repository_mismatch_never_dispatches_http(git_repo, tmp_path):
    with client_for(git_repo) as client:
        tool = GitHubDiffTool(git_repo, tmp_path, client=client)
        with pytest.raises(ToolFailure, match="does not match"):
            tool.execute(PRInput(repository="other/repo", pull_request=1), context())


def test_different_deployment_patch_fails_closed(git_repo, tmp_path):
    changed = git_repo.model_copy(
        update={"patched": git_repo.patched.model_copy(update={"revision": git_repo.baseline.revision})}
    )
    with client_for(git_repo) as client:
        tool = GitHubDiffTool(changed, tmp_path, client=client)
        with pytest.raises(ToolFailure, match="differs"):
            tool.execute(PRInput(repository="owner/repo", pull_request=1), context())


def test_diff_budget_does_not_silently_truncate(git_repo, tmp_path):
    # model_copy intentionally makes a very small test-only bound.
    limited = git_repo.model_copy(update={"max_patch_chars": 1})
    with client_for(git_repo) as client:
        with pytest.raises(ToolFailure, match="bounds"):
            GitHubDiffTool(limited, tmp_path, client=client).execute(
                PRInput(repository="owner/repo", pull_request=1), context()
            )


def test_application_paths_resolve_against_own_file():
    config = load_application(ROOT / "configs/application/saleor.json")
    assert Path(config.graph_snapshot_file).is_absolute()
    assert Path(config.repository_path).name == "saleor-storefront-upstream"


def test_artifact_write_is_content_addressed(tmp_path):
    first = save_artifact(tmp_path, "run", b"evidence", ".json")
    assert save_artifact(tmp_path, "run", b"evidence", ".json") == first
    assert len(list((tmp_path / "run/evidence").glob("*.tmp"))) == 0


def test_artifacts_remain_readable_beyond_windows_legacy_path_limit(tmp_path):
    root = tmp_path / ("nested-" + "x" * 70) / ("nested-" + "y" * 70)
    result = save_artifact(root, "r" * 80, b"long path evidence", ".observation.json")
    assert len(result["path"]) > 260
    assert Path(result["path"]).read_bytes() == b"long path evidence"


def test_application_schema_and_example_match():
    import jsonschema

    schema = json.loads((ROOT / "schemas/application.schema.json").read_text(encoding="utf-8"))
    example = json.loads((ROOT / "configs/application/saleor.json").read_text(encoding="utf-8"))
    jsonschema.validate(example, schema)
    ApplicationFile.model_validate(example)


@pytest.mark.parametrize(
    ("schema_name", "config_name", "model"),
    [
        ("graph.schema.json", "graph/saleor.json", GraphConfigFile),
        ("retrieval.schema.json", "retrieval/saleor.json", RetrievalConfigFile),
        ("ui.schema.json", "ui/saleor.json", PlaywrightUIConfigFile),
    ],
)
def test_component_schema_and_example_match(schema_name, config_name, model):
    import jsonschema

    schema = json.loads((ROOT / "schemas" / schema_name).read_text(encoding="utf-8"))
    example = json.loads((ROOT / "configs" / config_name).read_text(encoding="utf-8"))
    jsonschema.validate(example, schema)
    model.model_validate(example)


def local_application(app):
    return app.model_copy(
        update={
            "change_source": LocalGitSource(
                pull_request=1,
                base_revision=app.baseline.revision,
                head_revision=app.patched.revision,
            )
        }
    )


def test_local_diff_never_constructs_http_client_and_ignores_dirty_files(git_repo, tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Local Git must not create an HTTP client")

    monkeypatch.setattr(httpx, "Client", forbidden)
    app = local_application(git_repo).model_copy(update={"github_token_env": "MISSING_UNUSED_GITHUB_KEY"})
    (tmp_path / "checkout.ts").write_text("uncommitted change", encoding="utf-8")
    result = LocalGitDiffTool(app, tmp_path / "artifacts").execute(
        PRInput(repository="owner/repo", pull_request=1), context()
    )
    item = result.evidence[0]
    assert item.metadata["provider"] == "local_git"
    assert "export const total = 9;" in item.summary
    assert "uncommitted change" not in item.summary
    assert item.source.startswith("local-git:")
    assert any("not verified" in gap for gap in result.gaps)
    assert (tmp_path / "checkout.ts").read_text() == "uncommitted change"


def test_local_request_must_match_configured_change(git_repo, tmp_path):
    tool = LocalGitDiffTool(local_application(git_repo), tmp_path)
    with pytest.raises(ToolFailure, match="configured local change"):
        tool.execute(PRInput(repository="owner/repo", pull_request=2), context())


def test_local_missing_commit_fails_without_fetch(git_repo, tmp_path):
    app = local_application(git_repo)
    app = app.model_copy(
        update={"change_source": app.change_source.model_copy(update={"head_revision": "a" * 40})}
    )
    with pytest.raises(ToolFailure, match="local Git objects"):
        LocalGitDiffTool(app, tmp_path).execute(PRInput(repository="owner/repo", pull_request=1), context())


def test_local_mode_requires_full_commit_ids_and_right_provider(git_repo, tmp_path):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        LocalGitSource(pull_request=1, base_revision="main", head_revision="HEAD")
    with pytest.raises(ValueError, match="local_git"):
        LocalGitDiffTool(git_repo, tmp_path)


def test_local_empty_comparison_is_explicit(git_repo, tmp_path):
    app = git_repo.model_copy(
        update={"patched": git_repo.baseline.model_copy(update={"url": "https://patched.example"})}
    )
    result = LocalGitDiffTool(local_application(app), tmp_path).execute(
        PRInput(repository="owner/repo", pull_request=1), context()
    )
    assert not result.evidence
    assert any("no changed files" in gap for gap in result.gaps)
