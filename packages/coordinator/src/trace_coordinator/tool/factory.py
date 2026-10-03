"""Tool factory for fixture and live infrastructure strategies."""

import json
from contextlib import ExitStack

from trace_coordinator.config import FixtureProvider, LiveToolProvider
from trace_coordinator.domain.contracts import as_json_object
from trace_coordinator.domain.project import ApplicationConfig
from trace_coordinator.infrastructure.dependencies import BootstrapContext
from trace_coordinator.tool.implementations.fixture import FixtureTool
from trace_coordinator.tool.interface import Tool


class ToolFactory:
    def __init__(self, resources: ExitStack, context: BootstrapContext) -> None:
        self.resources = resources
        self.context = context

    def create(self, config: FixtureProvider | LiveToolProvider) -> list[Tool]:
        if config.provider == "fixture":
            return self._fixture_tools(config)
        return self._live_tools(self.context.require_application())

    def _fixture_tools(self, config: FixtureProvider) -> list[Tool]:
        document = as_json_object(json.loads(self.context.resolve(config.file).read_text(encoding="utf-8")))
        tools = document.get("tools")
        if not isinstance(tools, dict):
            raise ValueError("Fixture tool file requires a tools object")
        return [FixtureTool(name, result) for name, result in tools.items()]

    def _live_tools(self, application: ApplicationConfig) -> list[Tool]:
        tools = [
            self._change_tool(application),
            *self._knowledge_tools(application),
        ]
        self._add_attestation(tools, application)
        self._add_browser(tools, application)
        return tools

    def _change_tool(self, application: ApplicationConfig) -> Tool:
        if application.change_source.provider == "local_git":
            from trace_coordinator.tool.implementations.local_git import LocalGitDiffTool

            return LocalGitDiffTool(application, self.context.state_directory)

        from trace_coordinator.tool.implementations.github import GitHubDiffTool

        tool = GitHubDiffTool(application, self.context.state_directory)
        self.resources.callback(tool.close)
        return tool

    def _knowledge_tools(self, application: ApplicationConfig) -> list[Tool]:
        from trace_coordinator.tool.implementations.knowledge import KnowledgeTool

        return [
            KnowledgeTool(name, application, self.context.state_directory)
            for name in ("knowledge.graph", "knowledge.documents")
        ]

    def _add_attestation(self, tools: list[Tool], application: ApplicationConfig) -> None:
        if not (
            application.production_mode
            or any(
                deployment.attestation is not None
                for deployment in (application.baseline, application.patched)
            )
        ):
            return
        from trace_coordinator.tool.implementations.attestation import DeploymentAttestationTool

        tool = DeploymentAttestationTool(application)
        self.resources.callback(tool.close)
        tools.append(tool)

    def _add_browser(self, tools: list[Tool], application: ApplicationConfig) -> None:
        if not application.browser.enabled:
            return
        from trace_coordinator.tool.implementations.browser import BrowserSession, BrowserTool

        session = BrowserSession(application, self.context.state_directory)
        self.resources.callback(session.close)
        tools.extend(
            BrowserTool(name, session) for name in ("browser.navigate", "browser.observe", "browser.act")
        )
