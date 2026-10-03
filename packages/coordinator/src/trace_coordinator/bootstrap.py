"""Configuration builds adapters; the graph only depends on their interfaces."""

import json
from contextlib import ExitStack, contextmanager
from pathlib import Path

from trace_coordinator.adapters.fixtures import FixtureModel, FixtureTool
from trace_coordinator.api import Coordinator
from trace_coordinator.artifact_security import artifact_security
from trace_coordinator.config import load_config
from trace_coordinator.observability import CoordinatorObservability


@contextmanager
def create_coordinator(config_path: Path):
    path = config_path.resolve()
    config = load_config(path)
    if config.env_file:
        from dotenv import load_dotenv

        load_dotenv(path.parent / config.env_file, override=False)
    with ExitStack() as stack:
        state_root = (path.parent / config.state_directory).resolve()
        stack.enter_context(artifact_security(state_root, config.artifact_security))
        scenarios = []
        if config.tools.provider == "fixture":
            data = json.loads((path.parent / config.tools.file).read_text(encoding="utf-8"))
            tools = [FixtureTool(name, result) for name, result in data["tools"].items()]
        else:
            from trace_coordinator.adapters.knowledge import KnowledgeTool
            from trace_coordinator.application import load_application

            application = load_application(path.parent / config.tools.application_file)
            artifact_root = state_root
            if application.change_source.provider == "local_git":
                from trace_coordinator.adapters.local_git import LocalGitDiffTool

                changes = LocalGitDiffTool(application, artifact_root)
            else:
                from trace_coordinator.adapters.github import GitHubDiffTool

                changes = GitHubDiffTool(application, artifact_root)
                stack.callback(changes.close)
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
                from trace_coordinator.adapters.attestation import DeploymentAttestationTool

                attestation = DeploymentAttestationTool(application)
                stack.callback(attestation.close)
                tools.append(attestation)
            if application.browser.enabled:
                from trace_coordinator.adapters.browser import BrowserSession, BrowserTool

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
            from trace_coordinator.adapters.voucher_verification import VoucherScenario
            from trace_coordinator.verification import load_verification

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
            model = FixtureModel(model_data["decisions"])
        else:
            from trace_coordinator.adapters.langchain_model import LangChainModel

            model = LangChainModel(config.model)
            stack.callback(model.close)
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
