"""Approved Saleor voucher verification scenario."""

from pathlib import Path

from trace_coordinator.analysis_workflow.execution import ToolRegistry, ToolRuntime
from trace_coordinator.behavior_testing.scenarios.interface import BehaviorScenario
from trace_coordinator.behavior_testing.voucher_test import VoucherVerificationConfig, VoucherVerifier
from trace_coordinator.config import ScenarioBinding
from trace_coordinator.domain.contracts import VerificationResultPayload, as_json_object
from trace_coordinator.domain.errors import ToolFailure
from trace_coordinator.domain.models import ToolContext
from trace_coordinator.domain.project import ApplicationConfig
from trace_coordinator.storage.call_ledger import digest


class VoucherScenario(BehaviorScenario):
    """Verify one bounded voucher journey against pinned deployments."""

    required_calls = {
        "fixture.catalog": 1,
        "fixture.prepare": 3,
        "fixture.apply": 1,
        "fixture.observe": 3,
        "browser.navigate": 2,
        "browser.act": 5,
        "browser.check": 5,
    }

    def __init__(
        self,
        binding: ScenarioBinding,
        config: VoucherVerificationConfig,
        application: ApplicationConfig,
        root: Path,
    ) -> None:
        self.id = binding.id
        self.description = binding.description
        self.changed_paths = binding.changed_paths
        self.config = config
        self.app = application
        self.root = root
        self.version = "integrated-voucher-v1:" + digest(
            {
                "config": config.model_dump(mode="json"),
                "application": application.model_dump(mode="json"),
            }
        )

    def execute(self, runtime: ToolRuntime, context: ToolContext) -> VerificationResultPayload:
        from trace_coordinator.tool.implementations import saleor_voucher_tools

        changes = context.changes
        if (
            not self.app.browser.enabled
            or context.project_id != self.app.project_id
            or changes is None
            or changes.repository != self.app.repository
            or changes.analysis_base != self.app.baseline.revision
            or changes.analysis_head != self.app.patched.revision
        ):
            raise ToolFailure("Verification application differs from the pinned analysis scope")

        with saleor_voucher_tools.verification_tools(
            self.app, self.config, self.root, context.run_id
        ) as tools:
            shared = ToolRuntime(
                runtime.ledger,
                runtime.limits.model_copy(update={"retry_attempts": 1}),
                ToolRegistry(tools),
                model=None,
            )
            verifier = VoucherVerifier(self.config, self.app, runtime.ledger, runtime=shared)
            result = verifier.run(context.run_id, operation_prefix=f"verification:{self.id}:")
            attestations = {
                name: as_json_object(item)
                for name, item in context.runtime_attestations.items()
                if isinstance(item, dict)
            }
            if (
                set(attestations) == {"baseline", "patched"}
                and len({item["backend_fingerprint"] for item in attestations.values()}) == 1
                and all(
                    attestations[name]["revision"] == getattr(self.app, name).revision
                    for name in ("baseline", "patched")
                )
            ):
                result["comparison"] = {
                    "status": "SUPPORTED",
                    "reason": "Both URLs attested their configured source revisions and the same backend identity. The observed before/after difference supports attribution for the checks that ran.",
                }
            return result
