"""Validation for JSON-configured browser behavior checks."""

from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from impact_agent.config.validation.common import StrictSettings


class BehaviorActionConfig(StrictSettings):
    action: Literal["open", "click", "fill", "press"]
    url: str | None = None
    selector: str | None = None
    value: str | None = None

    @model_validator(mode="after")
    def validate_action_fields(self) -> "BehaviorActionConfig":
        if self.action == "open":
            if self.url is None or self.selector is not None or self.value is not None:
                raise ValueError("open action requires only url")
            parsed = urlsplit(self.url)
            if parsed.scheme != "https" or not parsed.hostname:
                raise ValueError("Behavior scenario URLs must use HTTPS and include a hostname")
        elif self.action == "click":
            if not self.selector or self.url is not None or self.value is not None:
                raise ValueError("click action requires only selector")
        elif self.action in {"fill", "press"}:
            if not self.selector or self.value is None or self.url is not None:
                raise ValueError(f"{self.action} action requires selector and value")
        return self


class BehaviorAssertionConfig(StrictSettings):
    kind: Literal["visible", "text_contains", "url_contains", "value_equals"]
    selector: str | None = None
    expected: str | None = None

    @model_validator(mode="after")
    def validate_assertion_fields(self) -> "BehaviorAssertionConfig":
        if self.kind == "url_contains":
            if self.selector is not None or not self.expected:
                raise ValueError("url_contains assertion requires only expected")
        elif self.kind == "visible":
            if not self.selector or self.expected is not None:
                raise ValueError("visible assertion requires only selector")
        elif not self.selector or self.expected is None:
            raise ValueError(f"{self.kind} assertion requires selector and expected")
        return self


class BehaviorScenarioConfig(StrictSettings):
    scenario_id: str = Field(min_length=1, max_length=100)
    target_routes: tuple[str, ...] = ()
    actions: tuple[BehaviorActionConfig, ...] = Field(min_length=1, max_length=30)
    assertions: tuple[BehaviorAssertionConfig, ...] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def validate_target_routes(self) -> "BehaviorScenarioConfig":
        if any(
            not route.startswith("/") or "?" in route or "#" in route
            for route in self.target_routes
        ):
            raise ValueError("target_routes must contain stable route paths without query strings")
        return self


class BehaviorConfig(StrictSettings):
    provider: Literal["playwright", "disabled"] = "disabled"
    scenarios: tuple[BehaviorScenarioConfig, ...] = Field(default=(), max_length=100)

    @model_validator(mode="after")
    def validate_scenario_ids(self) -> "BehaviorConfig":
        identifiers = [scenario.scenario_id for scenario in self.scenarios]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Behavior scenario IDs must be unique")
        return self
