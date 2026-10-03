"""Configuration builds adapters; the graph only depends on their interfaces."""

import json
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from trace_coordinator.application.coordinator import Coordinator
from trace_coordinator.application.interfaces import DecisionModel, Tool
from trace_coordinator.application.verification_stage import ApprovedScenario
from trace_coordinator.config import load_config
from trace_coordinator.infrastructure.adapters.fixtures import FixtureModel, FixtureTool
from trace_coordinator.infrastructure.observability import CoordinatorObservability
from trace_coordinator.security.artifact_security import artifact_security


@contextmanager
def create_coordinator(config_path: Path) -> Iterator[Coordinator]:
    path = config_path.resolve()
    config = load_config(path)
    if config.env_file:
        from dotenv import load_dotenv

        load_dotenv(path.parent / config.env_file, override=False)
    with ExitStack() as stack:
        state_root = (path.parent / config.state_directory).resolve()
        stack.enter_context(artifact_security(state_root, config.artifact_security))
        scenarios: list[ApprovedScenario] = []
        tools: list[Tool]
        if config.tools.provider == "fixture":
            data = json.loads((path.parent / config.tools.file).read_text(encoding="utf-8"))
            tools = [FixtureTool(name, result) for name, result in data["tools"].items()]
        else:
            from trace_coordinator.domain.project import load_application
            from trace_coordinator.infrastructure.adapters.knowledge import KnowledgeTool

            application = load_application(path.parent / config.tools.application_file)
            artifact_root = state_root
            if application.change_source.provider == "local_git":
                from trace_coordinator.infrastructure.adapters.local_git import LocalGitDiffTool

                changes: Tool = LocalGitDiffTool(application, artifact_root)
            else:
                from trace_coordinator.infrastructure.adapters.github import GitHubDiffTool

                github_changes = GitHubDiffTool(application, artifact_root)
                stack.callback(github_changes.close)
                changes = github_changes
            tools = [
                changes,
                *(
                    KnowledgeTool(name, application, artifact_root)
                    for name in ("knowledge.graph", "knowledge.documents")
                ),
            ]
            if application.production_mode or any(
                deployment.attestation is not None
                for deployment in (application.baseline, application.patched)
            ):
                from trace_coordinator.infrastructure.adapters.attestation import DeploymentAttestationTool

                attestation = DeploymentAttestationTool(application)
                stack.callback(attestation.close)
                tools.append(attestation)
            if application.browser.enabled:
                from trace_coordinator.infrastructure.adapters.browser import BrowserSession, BrowserTool

                session = BrowserSession(application, artifact_root)
                stack.callback(session.close)
                tools.extend(
                    BrowserTool(name, session)
                    for name in ("browser.navigate", "browser.observe", "browser.act")
                )
        if config.verification.enabled:
            if config.tools.provider != "live":
                raise ValueError(
                    "JSON verification requires live tools; inject synthetic scenarios through the Python API for offline tests"
                )
            from trace_coordinator.application.verification import load_verification
            from trace_coordinator.infrastructure.adapters.voucher_verification import VoucherScenario

            for binding in config.verification.scenarios:
                selected = load_verification(path.parent / binding.config_file)
                scenario_app = load_application(Path(selected.application_file))
                if scenario_app != application:
                    raise ValueError(
                        "Scenario and coordinator must use the same resolved application configuration"
                    )
                scenarios.append(VoucherScenario(binding, selected, application, artifact_root))
        if config.model.provider == "fixture":
            model_data = json.loads((path.parent / config.model.file).read_text(encoding="utf-8"))
            model: DecisionModel = FixtureModel(model_data["decisions"])
        else:
            from trace_coordinator.infrastructure.adapters.langchain_model import LangChainModel

            live_model = LangChainModel(config.model)
            stack.callback(live_model.close)
            model = live_model
        yield Coordinator(
            path.parent / config.state_directory,
            config.limits,
            tools,
            model,
            exploration=config.exploration,
            verification=config.verification,
            human_review=config.human_review,
            scenarios=scenarios,
            guardrails=config.guardrails,
            observability=CoordinatorObservability(config.observability),
        )
