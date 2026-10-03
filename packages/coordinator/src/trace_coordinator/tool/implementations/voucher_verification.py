"""Saleor sandbox fixture and read-only UI assertion tools.

Guest checkout IDs stay in memory. No checkout completion, payment, staff access,
or automatic mutation retries exist in this provider.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel

from trace_coordinator.domain.contracts import JsonObject, as_json_object, as_json_value
from trace_coordinator.domain.errors import ToolFailure
from trace_coordinator.domain.models import Evidence, Record, ToolContext, ToolResult
from trace_coordinator.domain.project import ApplicationConfig
from trace_coordinator.infrastructure.artifacts import save_artifact
from trace_coordinator.infrastructure.ledger import canonical, digest
from trace_coordinator.tool.implementations.browser import DESCRIBE, BrowserSession, BrowserTool
from trace_coordinator.tool.interface import Tool

if TYPE_CHECKING:
    from trace_coordinator.application.verification import VoucherVerificationConfig

FIELDS = """id voucherCode discount { amount currency } totalPrice { gross { amount currency } }
lines { quantity variant { id } } channel { slug } shippingAddress { country { code } }"""


class ObservedTotalPayload(TypedDict):
    value: str | None
    text: str


class EmptyInput(Record):
    pass


class FixtureInput(Record):
    environment: Literal["control", "baseline", "patched"]


class ProbeInput(Record):
    environment: Literal["baseline", "patched"]
    mode: Literal["initial", "applied", "removed"]
    expected_total: Decimal
    voucher_code: str


def normalize(checkout: JsonObject) -> JsonObject:
    discount = as_json_object(checkout["discount"])
    total_price = as_json_object(checkout["totalPrice"])
    gross = as_json_object(total_price["gross"])
    channel = as_json_object(checkout["channel"])
    address = as_json_object(checkout.get("shippingAddress") or {})
    lines_value = checkout["lines"]
    if not isinstance(lines_value, list):
        raise ToolFailure("Checkout lines are invalid")
    lines = [as_json_object(line) for line in lines_value]
    return as_json_object(
        dict(
            voucher=checkout["voucherCode"],
            discount=str(discount["amount"]),
            total=str(gross["amount"]),
            currency=gross["currency"],
            channel=channel["slug"],
            shipping_country=address.get("country"),
            lines=sorted(
                [
                    dict(variant=as_json_object(line["variant"])["id"], quantity=line["quantity"])
                    for line in lines
                ],
                key=lambda line: str(line["variant"]),
            ),
        )
    )


def _objects(value: object, *, field: str) -> list[JsonObject]:
    if not isinstance(value, list):
        raise ToolFailure(f"Sandbox field {field} is not a list")
    return [as_json_object(item) for item in value]


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise ToolFailure(f"Sandbox field {field} is not text")
    return value


class SaleorFixtures:
    def __init__(
        self,
        config: "VoucherVerificationConfig",
        application: ApplicationConfig,
        session: BrowserSession,
        root: Path,
        client: httpx.Client,
    ) -> None:
        self.config, self.app, self.session, self.root, self.client = (
            config,
            application,
            session,
            root,
            client,
        )
        self.variant: str | None = None
        self.checkouts: dict[Literal["control", "baseline", "patched"], str] = {}

    def request(self, query: str, variables: JsonObject, field: str) -> JsonObject:
        response = self.client.post(self.config.graphql_url, json={"query": query, "variables": variables})
        response.raise_for_status()
        result = as_json_object(response.json())
        data_value = result.get("data")
        if result.get("errors") or not isinstance(data_value, dict):
            raise ToolFailure("Sandbox API response is unavailable or rejected")
        payload = as_json_object(data_value)
        if payload.get(field) is None:
            raise ToolFailure("Sandbox API response omitted the requested field")
        data = as_json_object(payload[field])
        if data.get("errors"):
            raise ToolFailure("Sandbox rejected the fixture operation; eligibility must be checked")
        return data

    def execute(
        self,
        name: str,
        arguments: EmptyInput | FixtureInput,
        context: ToolContext,
    ) -> ToolResult:
        if context.project_id != self.app.project_id:
            raise ToolFailure("Fixture application is outside the project")
        public: JsonObject
        if name == "fixture.catalog":
            data = self.request(
                "query($slug:String!,$channel:String!){product(slug:$slug,channel:$channel){name slug variants{id name quantityAvailable}}}",
                as_json_object(dict(slug=self.config.product_slug, channel=self.config.channel)),
                "product",
            )
            matches = [
                v
                for v in _objects(data["variants"], field="variants")
                if v["name"] == self.config.variant_name
                and isinstance(v["quantityAvailable"], int)
                and v["quantityAvailable"] >= self.config.quantity
            ]
            if len(matches) != 1:
                raise ToolFailure("Expected one stocked variant with the configured name")
            self.variant = _text(matches[0]["id"], field="variant.id")
            public = as_json_object(
                dict(
                    product=data["slug"],
                    variant=self.variant,
                    variant_name=matches[0]["name"],
                    quantity_available=matches[0]["quantityAvailable"],
                    backend_version="UNAVAILABLE_PUBLIC_API",
                )
            )
        else:
            if not isinstance(arguments, FixtureInput):
                raise ToolFailure("Fixture operation requires an environment")
            environment = arguments.environment
            if name == "fixture.prepare":
                if not self.variant or environment in self.checkouts:
                    raise ToolFailure("Fixture already prepared or catalog was not checked")
                data = self.request(
                    "mutation($input:CheckoutCreateInput!){checkoutCreate(input:$input){errors{code} checkout{"
                    + FIELDS
                    + "}}}",
                    as_json_object(
                        {
                            "input": dict(
                                channel=self.config.channel,
                                lines=[dict(variantId=self.variant, quantity=self.config.quantity)],
                            )
                        }
                    ),
                    "checkoutCreate",
                )
                checkout = as_json_object(data["checkout"])
                checkout_id = _text(checkout["id"], field="checkout.id")
                self.checkouts[environment] = checkout_id
                if environment != "control":
                    deployment = getattr(self.app, environment).model_copy(
                        update={
                            "entry_path": self.config.checkout_path
                            + "?"
                            + urlencode({"checkout": checkout_id})
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
                        as_json_object(dict(id=self.checkouts[environment], code=self.config.voucher_code)),
                        "checkoutAddPromoCode",
                    )
                    checkout = as_json_object(data["checkout"])
                else:
                    checkout = self.request(
                        "query($id:ID!){checkout(id:$id){" + FIELDS + "}}",
                        as_json_object(dict(id=self.checkouts[environment])),
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

    def __init__(self, name: str, provider: SaleorFixtures) -> None:
        self.name, self.provider = name, provider
        self.input_model = EmptyInput if name == "fixture.catalog" else FixtureInput

    def execute(self, arguments: BaseModel, context: ToolContext) -> ToolResult:
        validated = self.input_model.model_validate(arguments)
        return self.provider.execute(self.name, validated, context)


class VoucherProbe:
    name = "browser.check"
    allowed_agents = frozenset({"coordinator"})
    input_model = ProbeInput
    description = "Read-only, bounded assertion of voucher state and total, with a fresh capture."
    version = "voucher-probe-v1"

    def __init__(self, session: BrowserSession, config: "VoucherVerificationConfig") -> None:
        self.session, self.config = session, config

    def execute(self, arguments: BaseModel, context: ToolContext) -> ToolResult:
        validated = ProbeInput.model_validate(arguments)
        return self.session.executor.submit(self._execute, validated, context).result()

    def _execute(self, arguments: ProbeInput, context: ToolContext) -> ToolResult:
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
            totals: list[ObservedTotalPayload] = [
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
                observed_value = totals[0]["value"] if len(totals) == 1 else None
                total_matches = bool(
                    len(totals) == 1
                    and observed_value is not None
                    and Decimal(observed_value) == arguments.expected_total
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
def verification_tools(
    application: ApplicationConfig,
    config: "VoucherVerificationConfig",
    root: Path,
    run_id: str,
) -> Iterator[list[Tool]]:
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
