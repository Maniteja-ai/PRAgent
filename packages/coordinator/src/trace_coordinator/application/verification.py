"""Bounded, deterministic voucher verification using the existing tool ledger.

The runner owns the checks; providers own fixture preparation and browser access.
Observed behavior is kept separate from attribution of that behavior to a PR.
"""

import json
from decimal import Decimal
from pathlib import Path
from typing import Literal

from filelock import FileLock
from pydantic import Field, field_validator, model_validator

from trace_coordinator.application.runtime import ToolRegistry, ToolRuntime
from trace_coordinator.config import CallLimits
from trace_coordinator.domain.errors import LimitReached, ToolFailure, UncertainExecution
from trace_coordinator.domain.models import Record, ToolContext
from trace_coordinator.domain.project import Deployment, load_application
from trace_coordinator.infrastructure.artifacts import save_artifact
from trace_coordinator.infrastructure.ledger import CallLedger, canonical, digest


class BehaviorRequirement(Record):
    id: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{2,79}$")
    statement: str = Field(min_length=1, max_length=500)
    environment: Literal["baseline", "patched"] = "patched"
    check_names: tuple[str, ...] = Field(min_length=1, max_length=10)


class VoucherVerificationConfig(Record):
    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    provider: Literal["saleor_playwright"] = "saleor_playwright"
    application_file: str
    state_directory: str
    graphql_url: str = Field(description="Public sandbox API; no staff token is required.")
    channel: str = Field(min_length=1)
    product_slug: str = Field(min_length=1)
    variant_name: str = Field(min_length=1)
    quantity: int = Field(default=1, ge=1, le=5)
    voucher_code: str = Field(min_length=1, max_length=100)
    discount_percent: Decimal = Field(
        gt=0, le=100, description="Fixture oracle, checked against a separate API control cart."
    )
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    money_locale: str = Field(
        default="en-US",
        min_length=2,
        max_length=30,
        description="Locale used by the storefront to format the visible total.",
    )
    input_name: str = "Discount code"
    apply_name: str = "Apply"
    remove_name: str = "Remove discount code"
    total_selector: str = Field(
        description="Read-only CSS selector for the visible total data element; must match exactly once."
    )
    checkout_path: str = "/checkout"
    assertion_timeout_seconds: int = Field(default=10, ge=1, le=30)
    api_timeout_seconds: int = Field(default=30, ge=1, le=60)
    requirements: tuple[BehaviorRequirement, ...] = Field(
        default=(),
        max_length=20,
        description="Reviewed requirement contracts mapped to deterministic check names.",
    )
    limits: CallLimits = Field(
        default_factory=lambda: CallLimits(total_calls=25, retry_attempts=1, max_run_seconds=600)
    )

    @field_validator("graphql_url")
    @classmethod
    def public_endpoint(cls, value):
        from urllib.parse import urlsplit

        parts = urlsplit(value)
        Deployment.public_url(f"{parts.scheme}://{parts.netloc}")
        if parts.query or parts.fragment or parts.username or parts.password:
            raise ValueError("Use a public API URL without credentials, query, or fragment")
        return value

    @field_validator("checkout_path")
    @classmethod
    def checkout_route(cls, value):
        from urllib.parse import urlsplit

        Deployment.relative_path(value)
        if urlsplit(value).query or urlsplit(value).fragment:
            raise ValueError("Checkout tokens are supplied privately by the fixture provider")
        return value

    @field_validator("limits")
    @classmethod
    def no_mutation_retries(cls, value):
        if value.retry_attempts != 1:
            raise ValueError("Guest-cart and browser mutations must not retry automatically")
        return value

    @model_validator(mode="after")
    def valid_requirements(self):
        planned = {
            "Empty voucher disables Apply",
            "Eligible voucher reaches backend",
            "Displayed discounted total and voucher state",
            "Remove voucher and restore total",
        }
        if len({item.id for item in self.requirements}) != len(self.requirements) or any(
            name not in planned for item in self.requirements for name in item.check_names
        ):
            raise ValueError("Requirements need unique IDs and known deterministic check names")
        return self


def load_verification(path):
    path = Path(path).resolve()
    config = VoucherVerificationConfig.model_validate_json(path.read_text(encoding="utf-8-sig"))
    return config.model_copy(
        update={
            name: str((path.parent / getattr(config, name)).resolve())
            for name in ("application_file", "state_directory")
        }
    )


def same_fixture(left, right):
    return left == right


def validate_oracle(before, after, config):
    """Fixture percentage and API result must agree before testing either UI."""
    initial = Decimal(str(before["total"]))
    discount = (initial * config.discount_percent / 100).quantize(Decimal("0.01"))
    return (
        before["currency"] == after["currency"] == config.currency
        and initial > 0
        and not before["voucher"]
        and Decimal(str(before["discount"])) == 0
        and after["voucher"] == config.voucher_code
        and Decimal(str(after["discount"])) == discount
        and Decimal(str(after["total"])) == initial - discount
        and before["lines"] == after["lines"]
    )


def behavior_report(checks, evidence, usage, *, stop_reason=None, requirements=()):
    planned = [
        (environment, name)
        for environment in ("baseline", "patched")
        for name in (
            "Empty voucher disables Apply",
            "Eligible voucher reaches backend",
            "Displayed discounted total and voucher state",
        )
    ]
    planned.append(("patched", "Remove voucher and restore total"))
    recorded = {(c["environment"], c["name"]) for c in checks}
    checks = [
        *checks,
        *[
            dict(environment=e, name=n, status="NOT_RUN", evidence_ids=[])
            for e, n in planned
            if (e, n) not in recorded
        ],
    ]
    requirement_results = []
    for requirement in requirements:
        selected = [
            check
            for check in checks
            if check["environment"] == requirement.environment and check["name"] in requirement.check_names
        ]
        statuses = {check["status"] for check in selected}
        status = (
            "PASS"
            if len(selected) == len(requirement.check_names) and statuses == {"PASS"}
            else "FAIL"
            if "FAIL" in statuses
            else "BLOCKED"
        )
        requirement_results.append(
            {
                **requirement.model_dump(mode="json"),
                "status": status,
                "evidence_ids": list(
                    dict.fromkeys(ref for check in selected for ref in check["evidence_ids"])
                ),
            }
        )
    return dict(
        schema_version=1,
        stage="voucher_behavior_v1",
        status="BLOCKED" if stop_reason else "COMPLETED",
        checks=checks,
        requirements=requirement_results,
        evidence=evidence,
        tool_usage=usage,
        stop_reason=stop_reason,
        comparison={
            "status": "INCONCLUSIVE",
            "reason": "Deployment build identity and backend version are not runtime-attested. Per-environment observations are valid; PR causality is not established.",
        },
        not_run=[
            "Invalid voucher",
            "Shipping prerequisites",
            "Quantity changes",
            "Baseline removal",
            "Order submission",
        ],
        llm_calls=0,
    )


def verification_markdown(report):
    lines = [
        "# Voucher behavior verification",
        "",
        f"Execution: {report['status']}",
        "",
        "| Environment | Check | Result |",
        "| --- | --- | --- |",
    ]
    lines.extend(f"| {c['environment']} | {c['name']} | {c['status']} |" for c in report["checks"])
    if report.get("requirements"):
        lines.extend(
            ["", "## Requirement contracts", "", "| ID | Requirement | Result |", "| --- | --- | --- |"]
        )
        lines.extend(
            f"| {item['id']} | {item['statement']} | {item['status']} |" for item in report["requirements"]
        )
    lines.extend(
        [
            "",
            report["comparison"]["reason"],
            "",
            "Not run: " + ", ".join(report["not_run"]),
            "",
            "No payment or order was submitted. Guest fixture carts remain in the sandbox.",
            "",
            "| Tool | Attempts |",
            "| --- | --- |",
        ]
    )
    lines.extend(f"| {row['tool']} | {row['attempts']} |" for row in report["tool_usage"])
    if report["stop_reason"]:
        lines.extend(["", "Stopped: " + report["stop_reason"]])
    return "\n".join(lines) + "\n"


class VoucherVerifier:
    """Single stable coordinator identity, with every external operation counted."""

    def __init__(self, config, application, ledger, tools=None, *, runtime=None):
        self.config, self.app, self.ledger = config, application, ledger
        self.runtime = runtime or ToolRuntime(ledger, config.limits, ToolRegistry(tools), model=None)

    def run(self, run_id, *, operation_prefix=""):
        context = ToolContext(run_id=run_id, project_id=self.app.project_id, agent_id="coordinator")
        evidence, checks = {}, []

        def call(name, operation, **arguments):
            result = self.runtime.call_tool(context, operation_prefix + operation, name, arguments)
            for item in result.evidence:
                evidence[item.id] = item.model_dump(mode="json")
            return result.evidence[-1]

        def check(name, environment, passed, refs, *, blocked=False):
            checks.append(
                dict(
                    name=name,
                    environment=environment,
                    status="BLOCKED" if blocked else ("PASS" if passed else "FAIL"),
                    evidence_ids=refs,
                )
            )

        def probe(environment, mode, total, operation):
            item = call(
                "browser.check",
                operation,
                environment=environment,
                mode=mode,
                expected_total=str(total),
                voucher_code=self.config.voucher_code,
            )
            return item, json.loads(item.summary)

        def act(environment, observation, name, action, operation, value=""):
            screen = json.loads(observation.summary)
            matches = [e for e in screen["elements"] if e["name"] == name and not e["disabled"]]
            if len(matches) != 1:
                raise ToolFailure("Required enabled browser control is absent or ambiguous")
            return call(
                "browser.act",
                operation,
                environment=environment,
                snapshot_id=observation.id,
                element_id=matches[0]["id"],
                action=action,
                value=value,
            )

        stop_reason = None
        try:
            call("fixture.catalog", "catalog")
            initial = {}
            for environment in ("control", "baseline", "patched"):
                item = call("fixture.prepare", f"prepare-{environment}", environment=environment)
                initial[environment] = (item, json.loads(item.summary))
            control = call("fixture.apply", "control-apply", environment="control")
            oracle = json.loads(control.summary)
            if not validate_oracle(initial["control"][1], oracle, self.config) or not all(
                same_fixture(initial["control"][1], initial[e][1]) for e in ("baseline", "patched")
            ):
                raise ToolFailure("Fixture eligibility, expected discount, or equivalence check failed")
            for environment in ("baseline", "patched"):
                start, state = initial[environment]
                call("browser.navigate", f"navigate-{environment}", environment=environment)
                ready, observation = probe(environment, "initial", state["total"], f"ready-{environment}")
                check(
                    "Empty voucher disables Apply",
                    environment,
                    observation["conditions_met"],
                    [ready.id],
                    blocked=not observation["ready"],
                )
                if not observation["conditions_met"]:
                    raise ToolFailure("Initial checkout does not satisfy fixture/UI preconditions")
                captured = evidence[observation["capture_id"]]
                from trace_coordinator.domain.models import Evidence

                filled = act(
                    environment,
                    Evidence.model_validate(captured),
                    self.config.input_name,
                    "fill",
                    f"fill-{environment}",
                    self.config.voucher_code,
                )
                act(environment, filled, self.config.apply_name, "click", f"apply-{environment}")
                observed, outcome = probe(environment, "applied", oracle["total"], f"applied-{environment}")
                actual = call("fixture.observe", f"api-{environment}", environment=environment)
                backend = json.loads(actual.summary)
                api_pass = backend == oracle
                refs = [observed.id, actual.id, control.id]
                check(
                    "Eligible voucher reaches backend",
                    environment,
                    api_pass,
                    refs,
                    blocked=not outcome["ready"],
                )
                check(
                    "Displayed discounted total and voucher state",
                    environment,
                    outcome["conditions_met"] and api_pass,
                    refs,
                    blocked=not outcome["ready"],
                )
                if environment == "patched":
                    if not api_pass or not outcome["conditions_met"]:
                        check("Remove voucher and restore total", environment, False, refs, blocked=True)
                        continue
                    captured = Evidence.model_validate(evidence[outcome["capture_id"]])
                    act(environment, captured, self.config.remove_name, "click", "remove-patched")
                    removed, removal = probe(environment, "removed", state["total"], "removed-patched")
                    restored = call("fixture.observe", "api-removed-patched", environment=environment)
                    check(
                        "Remove voucher and restore total",
                        environment,
                        removal["conditions_met"] and json.loads(restored.summary) == state,
                        [removed.id, restored.id, start.id],
                        blocked=not removal["ready"],
                    )
        except (ToolFailure, LimitReached, UncertainExecution) as exc:
            stop_reason = str(exc)
        return behavior_report(
            checks,
            evidence,
            self.ledger.usage(run_id),
            stop_reason=stop_reason,
            requirements=self.config.requirements,
        )


def run_verification(config_path, run_id):
    from trace_coordinator.infrastructure.adapters.voucher_verification import verification_tools

    if (
        not run_id
        or len(run_id) > 80
        or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in run_id)
    ):
        raise ValueError("Use a short run ID containing letters, digits, hyphens or underscores")
    config = load_verification(config_path)
    app = load_application(Path(config.application_file))
    root = Path(config.state_directory)
    root.mkdir(parents=True, exist_ok=True)
    ledger = CallLedger(root / "verification.sqlite")
    fingerprint = digest(
        {
            "config": config.model_dump(mode="json"),
            "app": app.model_dump(mode="json"),
            "version": "voucher-behavior-v1",
        }
    )
    with FileLock(str(root / (run_id + ".lock")), timeout=1):
        ledger.register(run_id, fingerprint)
        pointer = root / (run_id + ".json")
        if pointer.exists():
            from trace_coordinator.application.mapping import verified_bytes

            return json.loads(verified_bytes(json.loads(pointer.read_text())))
        if ledger.usage(run_id):
            raise UncertainExecution(
                "Previous verification has attempts but no final report; do not recreate carts or replay browser actions automatically"
            )
        with verification_tools(app, config, root, run_id) as tools:
            result = VoucherVerifier(config, app, ledger, tools).run(run_id)
        result.update(
            run_id=run_id,
            project_id=app.project_id,
            config_fingerprint=fingerprint,
            deployments={e: getattr(app, e).model_dump(mode="json") for e in ("baseline", "patched")},
            fixture={
                "api": config.graphql_url,
                "channel": config.channel,
                "product": config.product_slug,
                "variant": config.variant_name,
                "quantity": config.quantity,
                "voucher": config.voucher_code,
            },
        )
        saved = save_artifact(root, run_id, canonical(result).encode(), ".behavior.json")
        pointer.write_text(canonical(saved), encoding="utf-8")
        return result
