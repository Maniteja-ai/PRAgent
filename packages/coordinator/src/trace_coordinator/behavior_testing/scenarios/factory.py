"""Approved behavioral-scenario factory."""

from pathlib import Path

from trace_coordinator.behavior_testing.scenarios.implementations.voucher import VoucherScenario
from trace_coordinator.behavior_testing.scenarios.interface import BehaviorScenario
from trace_coordinator.behavior_testing.voucher_test import load_verification
from trace_coordinator.config import VerificationPolicy
from trace_coordinator.domain.project import load_application
from trace_coordinator.startup import StartupContext


class BehaviorScenarioFactory:
    def __init__(self, context: StartupContext) -> None:
        self.context = context

    def create(self, policy: VerificationPolicy) -> list[BehaviorScenario]:
        if not policy.enabled:
            return []
        application = self.context.require_application()

        scenarios: list[BehaviorScenario] = []
        for binding in policy.scenarios:
            selected = load_verification(self.context.resolve(binding.config_file))
            if load_application(Path(selected.application_file)) != application:
                raise ValueError(
                    "Scenario and coordinator must use the same resolved application configuration"
                )
            scenarios.append(VoucherScenario(binding, selected, application, self.context.state_directory))
        return scenarios
