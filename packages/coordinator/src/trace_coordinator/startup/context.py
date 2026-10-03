"""Resolved paths and shared application data used during startup."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from trace_coordinator.config import CoordinatorConfig
from trace_coordinator.domain.project import ApplicationConfig, load_application


@dataclass(frozen=True)
class StartupContext:
    """Immutable values shared while one coordinator instance is assembled."""

    config_path: Path
    state_directory: Path
    application: ApplicationConfig | None

    @classmethod
    def create(cls, config_path: Path, config: CoordinatorConfig) -> StartupContext:
        resolved = config_path.resolve()
        application = (
            load_application(resolved.parent / config.tool_provider.application_config_file)
            if config.tool_provider.provider == "live"
            else None
        )
        return cls(
            config_path=resolved,
            state_directory=(resolved.parent / config.state_directory).resolve(),
            application=application,
        )

    def resolve(self, relative_path: str) -> Path:
        """Resolve one configuration-relative path."""

        return (self.config_path.parent / relative_path).resolve()

    def require_application(self) -> ApplicationConfig:
        """Return the live application or reject an incompatible provider combination."""

        if self.application is None:
            raise ValueError("This feature requires live tools and an application configuration")
        return self.application
