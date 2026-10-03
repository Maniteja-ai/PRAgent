"""Composition root: validate configuration and assemble one coordinator."""

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from trace_coordinator.application.coordinator import Coordinator
from trace_coordinator.config import load_config
from trace_coordinator.infrastructure.factories import (
    BootstrapContext,
    ModelFactory,
    ScenarioFactory,
    ToolFactory,
)
from trace_coordinator.infrastructure.observability import CoordinatorObservability
from trace_coordinator.security.artifact_security import artifact_security


@contextmanager
def create_coordinator(config_path: Path) -> Iterator[Coordinator]:
    path = config_path.resolve()
    config = load_config(path)
    if config.env_file:
        from dotenv import load_dotenv

        load_dotenv(path.parent / config.env_file, override=False)
    with ExitStack() as resources:
        context = BootstrapContext.create(path, config)
        resources.enter_context(artifact_security(context.state_directory, config.artifact_security))
        tools = ToolFactory(resources, context).create(config.tool_provider)
        model = ModelFactory(resources, context).create(config.model)
        scenarios = ScenarioFactory(context).create(config.verification)
        yield Coordinator(
            context.state_directory,
            config.limits,
            tools,
            model,
            ui_exploration=config.ui_exploration,
            verification=config.verification,
            human_review=config.human_review,
            scenarios=scenarios,
            guardrails=config.guardrails,
            observability=CoordinatorObservability(config.observability),
        )
