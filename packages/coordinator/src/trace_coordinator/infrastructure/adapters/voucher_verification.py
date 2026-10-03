"""Saleor sandbox fixture and read-only UI assertion tools.

Guest checkout IDs stay in memory. No checkout completion, payment, staff access,
or automatic mutation retries exist in this provider.
"""

import time
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from typing import Literal
from urllib.parse import urlencode

import httpx

from trace_coordinator.domain.contracts import as_json_value
from trace_coordinator.domain.errors import ToolFailure
from trace_coordinator.domain.models import Evidence, Record, ToolResult
from trace_coordinator.infrastructure.adapters.browser import DESCRIBE, BrowserSession, BrowserTool
from trace_coordinator.infrastructure.artifacts import save_artifact
from trace_coordinator.infrastructure.ledger import canonical, digest

FIELDS = """id voucherCode discount { amount currency } totalPrice { gross { amount currency } }
lines { quantity variant { id } } channel { slug } shippingAddress { country { code } }"""


class VoucherScenario:
    """Approved adapter: reuse the parent run, agent, limits and attempt database."""

    required_calls = {
        "fixture.catalog": 1,
        "fixture.prepare": 3,
        "fixture.apply": 1,
        "fixture.observe": 3,
        "browser.navigate": 2,
        "browser.act": 5,
        "browser.check": 5,
    }

    def __init__(self, binding, config, application, root):
        self.id, self.description, self.changed_paths = binding.id, binding.description, binding.changed_paths
        self.config, self.app, self.root = config, application, root
        self.version = "integrated-voucher-v1:" + digest(
            {"config": config.model_dump(mode="json"), "application": application.model_dump(mode="json")}
        )

    def execute(self, runtime, context):
        from trace_coordinator.application.runtime import ToolRegistry, ToolRuntime
        from trace_coordinator.application.verification import VoucherVerifier

        changes = context.changes
        if (
            not self.app.browser.enabled
            or context.project_id != self.app.project_id
            or changes is None
            or (
                changes.repository != self.app.repository
                or changes.analysis_base != self.app.baseline.revision
                or changes.analysis_head != self.app.patched.revision
            )
        ):
            raise ToolFailure("Verification application differs from the pinned analysis scope")
        with verification_tools(self.app, self.config, self.root, context.run_id) as tools:
            shared = ToolRuntime(
                runtime.ledger,
                runtime.limits.model_copy(update={"retry_attempts": 1}),
                ToolRegistry(tools),
                model=None,
            )
            verifier = VoucherVerifier(self.config, self.app, runtime.ledger, runtime=shared)
            result = verifier.run(context.run_id, operation_prefix=f"verification:{self.id}:")
            attestations = context.runtime_attestations
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


class EmptyInput(Record):
    pass


class FixtureInput(Record):
    environment: Literal["control", "baseline", "patched"]


class ProbeInput(Record):
    environment: Literal["baseline", "patched"]
    mode: Literal["initial", "applied", "removed"]
    expected_total: Decimal
    voucher_code: str


def normalize(checkout):
    return dict(
        voucher=checkout["voucherCode"],
        discount=str(checkout["discount"]["amount"]),
        total=str(checkout["totalPrice"]["gross"]["amount"]),
        currency=checkout["totalPrice"]["gross"]["currency"],
        channel=checkout["channel"]["slug"],
        shipping_country=(checkout["shippingAddress"] or {}).get("country"),
        lines=sorted(
            [dict(variant=line["variant"]["id"], quantity=line["quantity"]) for line in checkout["lines"]],
            key=lambda line: line["variant"],
        ),
    )


class SaleorFixtures:
    def __init__(self, config, application, session, root, client):
        self.config, self.app, self.session, self.root, self.client = (
            config,
            application,
            session,
            root,
            client,
        )
        self.variant = None
        self.checkouts = {}

    def request(self, query, variables, field):
        response = self.client.post(self.config.graphql_url, json={"query": query, "variables": variables})
        response.raise_for_status()
        result = response.json()
        if result.get("errors") or not result.get("data") or result["data"].get(field) is None:
            raise ToolFailure("Sandbox API response is unavailable or rejected")
        data = result["data"][field]
        if data.get("errors"):
            raise ToolFailure("Sandbox rejected the fixture operation; eligibility must be checked")
        return data

    def execute(self, name, arguments, context):
        if context.project_id != self.app.project_id:
            raise ToolFailure("Fixture application is outside the project")
        if name == "fixture.catalog":
            data = self.request(
                "query($slug:String!,$channel:String!){product(slug:$slug,channel:$channel){name slug variants{id name quantityAvailable}}}",
                dict(slug=self.config.product_slug, channel=self.config.channel),
                "product",
            )
            matches = [
                v
                for v in data["variants"]
                if v["name"] == self.config.variant_name
                and isinstance(v["quantityAvailable"], int)
                and v["quantityAvailable"] >= self.config.quantity
            ]
            if len(matches) != 1:
                raise ToolFailure("Expected one stocked variant with the configured name")
            self.variant = matches[0]["id"]
            public = dict(
                product=data["slug"],
                variant=self.variant,
                variant_name=matches[0]["name"],
                quantity_available=matches[0]["quantityAvailable"],
                backend_version="UNAVAILABLE_PUBLIC_API",
            )
        else:
            environment = arguments.environment
            if name == "fixture.prepare":
                if not self.variant or environment in self.checkouts:
                    raise ToolFailure("Fixture already prepared or catalog was not checked")
                data = self.request(
                    "mutation($input:CheckoutCreateInput!){checkoutCreate(input:$input){errors{code} checkout{"
                    + FIELDS
                    + "}}}",
                    {
                        "input": dict(
                            channel=self.config.channel,
                            lines=[dict(variantId=self.variant, quantity=self.config.quantity)],
                        )
                    },
                    "checkoutCreate",
                )
                checkout = data["checkout"]
                self.checkouts[environment] = checkout["id"]
                if environment != "control":
                    deployment = getattr(self.app, environment).model_copy(
                        update={
                            "entry_path": self.config.checkout_path
                            + "?"
                            + urlencode({"checkout": checkout["id"]})
                        }
                    )
                    self.session.app = self.session.app.model_copy(update={environment: deployment})
            else:
                if environment not in self.checkouts:
                    raise ToolFailure("No live checkout in this verification session")
                if name == "fixture.apply":
                    if environment != "control":
                        raise ToolFailure(
                            "Only the separate oracle cart may be changed directly through the API"
                        )
                    data = self.request(
                        "mutation($id:ID!,$code:String!){checkoutAddPromoCode(id:$id,promoCode:$code){errors{code} checkout{"
                        + FIELDS
                        + "}}}",
                        dict(id=self.checkouts[environment], code=self.config.voucher_code),
                        "checkoutAddPromoCode",
                    )
                    checkout = data["checkout"]
                else:
                    checkout = self.request(
                        "query($id:ID!){checkout(id:$id){" + FIELDS + "}}",
                        dict(id=self.checkouts[environment]),
                        "checkout",
                    )
            public = normalize(checkout)
            if (
                public["channel"] != self.config.channel
                or public["lines"] != [dict(variant=self.variant, quantity=self.config.quantity)]
                or public["shipping_country"] is not None
            ):
                raise ToolFailure("Cart state differs from the configured guest fixture")
        saved = save_artifact(self.root, context.run_id, canonical(public).encode(), ".fixture.json")
        return ToolResult(
            evidence=(
                Evidence(
                    id="fixture:" + digest([name, arguments.model_dump(), saved["sha256"]])[:24],
                    project_id=context.project_id,
                    kind="fixture",
                    source=self.config.graphql_url,
                    summary=canonical(public),
                    metadata={
                        "artifact": as_json_value(saved),
                        "operation": name,
                        "captured_at": time.time(),
                    },
                ),
            )
        )


class FixtureTool:
    allowed_agents = frozenset({"coordinator"})
    description = "One bounded sandbox fixture operation. Never creates an order."
    version = "saleor-fixture-v1"

    def __init__(self, name, provider):
        self.name, self.provider = name, provider
        self.input_model = EmptyInput if name == "fixture.catalog" else FixtureInput

    def execute(self, arguments, context):
        return self.provider.execute(self.name, arguments, context)


class VoucherProbe:
    name = "browser.check"
    allowed_agents = frozenset({"coordinator"})
    input_model = ProbeInput
    description = "Read-only, bounded assertion of voucher state and total, with a fresh capture."
    version = "voucher-probe-v1"

    def __init__(self, session, config):
        self.session, self.config = session, config

    def execute(self, arguments, context):
        return self.session.executor.submit(self._execute, arguments, context).result()

    def _execute(self, arguments, context):
        if (
            context.project_id != self.session.app.project_id
            or self.session.run_id != context.run_id
            or arguments.environment not in self.session.pages
        ):
            raise ToolFailure("No live browser session for this project and run")
        page = self.session.pages[arguments.environment]
        expected_text = page.evaluate(
            "([locale,currency,amount]) => new Intl.NumberFormat(locale,{style:'currency',currency}).format(Number(amount))",
            [self.config.money_locale, self.config.currency, str(arguments.expected_total)],
        )
        deadline = time.monotonic() + self.config.assertion_timeout_seconds
        while True:
            totals = [
                {"value": node.get_attribute("value"), "text": node.inner_text()}
                for node in page.locator(self.config.total_selector).all()
                if node.is_visible()
            ]
            controls = [
                node.evaluate(DESCRIBE)
                for node in page.locator('button,input:not([type="hidden"])').all()
                if node.is_visible()
            ]
            apply = [c for c in controls if c["name"].strip() == self.config.apply_name]
            field = [c for c in controls if c["name"].strip() == self.config.input_name]
            remove = [c for c in controls if c["name"].strip() == self.config.remove_name]
            try:
                total_matches = (
                    len(totals) == 1
                    and Decimal(totals[0]["value"]) == arguments.expected_total
                    and " ".join(totals[0]["text"].split()) == " ".join(expected_text.split())
                )
            except (InvalidOperation, TypeError):
                total_matches = False
            voucher_visible = any(
                n.is_visible() for n in page.get_by_text(arguments.voucher_code, exact=True).all()
            )
            ready = len(totals) == 1 and (
                (len(remove) == 1 and not remove[0]["disabled"])
                or (len(apply) == 1 and len(field) == 1 and not field[0]["disabled"])
            )
            if arguments.mode == "applied":
                state_matches = len(remove) == 1 and voucher_visible and not remove[0]["disabled"]
            else:
                state_matches = (
                    len(apply) == len(field) == 1
                    and apply[0]["disabled"]
                    and not field[0]["filled"]
                    and not voucher_visible
                    and not remove
                )
            passed = bool(ready and total_matches and state_matches)
            if passed or time.monotonic() >= deadline:
                break
            page.wait_for_timeout(100)
        captured = self.session._capture(
            page,
            arguments.environment,
            context,
            self.session.snapshots.get(arguments.environment),
            {"tool": self.name},
        )
        public = dict(
            mode=arguments.mode,
            ready=ready,
            conditions_met=passed,
            total_matches=total_matches,
            voucher_state_matches=bool(state_matches),
            observed_total=totals,
            expected_total=str(arguments.expected_total),
            capture_id=captured.evidence[0].id,
            environment=arguments.environment,
        )
        saved = save_artifact(
            self.session.root, context.run_id, canonical(public).encode(), ".assertion.json"
        )
        assertion = Evidence(
            id="assertion:" + saved["sha256"][:24],
            project_id=context.project_id,
            kind="verification",
            summary=canonical(public),
            source=captured.evidence[0].source,
            metadata={"artifact": as_json_value(saved)},
        )
        return ToolResult(evidence=(*captured.evidence, assertion))


@contextmanager
def verification_tools(application, config, root, run_id):
    # Exactly these actions are authorized for this test, not order submission.
    app = application.model_copy(
        update={
            "browser": application.browser.model_copy(
                update={
                    "allowed_button_names": (config.apply_name, config.remove_name),
                    "allowed_button_test_ids": (),
                    "allowed_field_names": (config.input_name,),
                }
            )
        }
    )
    session = BrowserSession(app, root)
    with httpx.Client(timeout=config.api_timeout_seconds, follow_redirects=False) as client:
        try:
            provider = SaleorFixtures(config, app, session, root, client)
            yield [
                *[
                    FixtureTool(name, provider)
                    for name in ("fixture.catalog", "fixture.prepare", "fixture.apply", "fixture.observe")
                ],
                BrowserTool("browser.navigate", session),
                BrowserTool("browser.act", session),
                VoucherProbe(session, config),
            ]
        finally:
            session.close()
