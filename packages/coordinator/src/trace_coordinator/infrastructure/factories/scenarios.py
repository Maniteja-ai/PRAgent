"""Approved behavioral-scenario factory."""

from pathlib import Path

from trace_coordinator.application.verification import load_verification
from trace_coordinator.application.verification_stage import ApprovedScenario
from trace_coordinator.config import VerificationPolicy
from trace_coordinator.domain.project import load_application
from trace_coordinator.infrastructure.factories.context import BootstrapContext


class ScenarioFactory:
    def __init__(self, context: BootstrapContext) -> None:
        self.context = context

    def create(self, policy: VerificationPolicy) -> list[ApprovedScenario]:
        if not policy.enabled:
            return []
        application = self.context.require_application()

        from trace_coordinator.infrastructure.adapters.voucher_verification import VoucherScenario

        scenarios: list[ApprovedScenario] = []
        for binding in policy.scenarios:
            selected = load_verification(self.context.resolve(binding.config_file))
            if load_application(Path(selected.application_file)) != application:
                raise ValueError(
                    "Scenario and coordinator must use the same resolved application configuration"
                )
            scenarios.append(VoucherScenario(binding, selected, application, self.context.state_directory))
        return scenarios
