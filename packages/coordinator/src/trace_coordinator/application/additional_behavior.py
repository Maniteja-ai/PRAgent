"""Bounded Saleor API oracles for additional approved behavior scenarios."""

import re
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

import httpx
from filelock import FileLock
from pydantic import Field, model_validator

from trace_coordinator.config import CallLimits
from trace_coordinator.domain.errors import ToolFailure, UncertainExecution
from trace_coordinator.domain.models import Record
from trace_coordinator.domain.project import Deployment
from trace_coordinator.infrastructure.artifacts import save_artifact
from trace_coordinator.infrastructure.ledger import CallLedger, canonical, digest

FIELDS = """voucherCode discount { amount currency } shippingPrice { gross { amount currency } }
totalPrice { gross { amount currency } } lines { id quantity variant { id } }
channel { slug } shippingAddress { country { code } }
delivery { shippingMethod { id name } }"""


class InvalidVoucher(Record):
    scenario: Literal["invalid_voucher"]
    invalid_code: str = Field(min_length=1, max_length=100)


class QuantityChange(Record):
    scenario: Literal["quantity_change"]
    voucher_code: str = Field(min_length=1, max_length=100)
    initial_quantity: int = Field(default=1, ge=1, le=4)
    target_quantity: int = Field(default=2, ge=2, le=5)

    @model_validator(mode="after")
    def changed_quantity(self):
        if self.target_quantity == self.initial_quantity:
            raise ValueError("Target quantity must differ from initial quantity")
        return self


class ShippingAddress(Record):
    first_name: str = Field(min_length=1, max_length=50)
    last_name: str = Field(min_length=1, max_length=50)
    street_address_1: str = Field(min_length=1, max_length=100)
    city: str = Field(min_length=1, max_length=100)
    postal_code: str = Field(min_length=1, max_length=20)
    country: str = Field(pattern=r"^[A-Z]{2}$")


class ShippingPrerequisite(Record):
    scenario: Literal["shipping_prerequisite"]
    voucher_code: str = Field(min_length=1, max_length=100)
    shipping_method_name: str = Field(min_length=1, max_length=200)
    address: ShippingAddress


Scenario = Annotated[InvalidVoucher | QuantityChange | ShippingPrerequisite, Field(discriminator="scenario")]


class AdditionalBehaviorConfig(Record):
    schema_reference: str | None = Field(default=None, alias="$schema", exclude=True)
    schema_version: Literal[1] = 1
    graphql_url: str
    channel: str = Field(min_length=1)
    product_slug: str = Field(min_length=1)
    variant_name: str = Field(min_length=1)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    state_directory: str
    scenario: Scenario
    timeout_seconds: float = Field(default=30, gt=0, le=60)
    limits: CallLimits = Field(
        default_factory=lambda: CallLimits(total_calls=20, retry_attempts=1, max_run_seconds=300)
    )

    @model_validator(mode="after")
    def safe(self):
        parts = urlsplit(self.graphql_url)
        Deployment.public_url(f"{parts.scheme}://{parts.netloc}")
        if parts.query or parts.fragment or parts.username or parts.password:
            raise ValueError("Use a public GraphQL URL without credentials")
        if self.limits.retry_attempts != 1:
            raise ValueError("Behavior mutations must not retry automatically")
        return self


def load_additional_behavior(path):
    path = Path(path).resolve()
    config = AdditionalBehaviorConfig.model_validate_json(path.read_text(encoding="utf-8-sig"))
    return config.model_copy(
        update={"state_directory": str((path.parent / config.state_directory).resolve())}
    )


def public(checkout):
    return {
        "voucher": checkout.get("voucherCode"),
        "discount": checkout["discount"],
        "shipping": checkout.get("shippingPrice"),
        "total": checkout["totalPrice"]["gross"],
        "lines": sorted(
            [{"variant": line["variant"]["id"], "quantity": line["quantity"]} for line in checkout["lines"]],
            key=lambda row: row["variant"],
        ),
        "channel": checkout["channel"]["slug"],
        "shipping_country": ((checkout.get("shippingAddress") or {}).get("country") or {}).get("code"),
        "delivery_method": ((checkout.get("delivery") or {}).get("shippingMethod") or {}).get("name"),
    }


class SaleorScenarioRunner:
    def __init__(self, config, root, run_id, *, client=None):
        self.config, self.root, self.run_id = config, Path(root), run_id
        self.client = client or httpx.Client(timeout=config.timeout_seconds, follow_redirects=False)
        self.owns_client = client is None
        self.ledger = CallLedger(self.root / "additional-behavior.sqlite")

    def close(self):
        if self.owns_client:
            self.client.close()

    def call(self, operation, query, variables, field):
        self.ledger.reserve(
            self.run_id,
            operation,
            1,
            "behavior-runner",
            "saleor." + operation,
            {
                "field": field,
                "request_sha256": digest({"query": query, "variables": variables}),
            },
            self.config.limits,
        )
        try:
            response = self.client.post(
                self.config.graphql_url, json={"query": query, "variables": variables}
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("errors") or not payload.get("data") or payload["data"].get(field) is None:
                raise ToolFailure("Saleor behavior operation failed")
            value = payload["data"][field]
        except Exception as exc:
            self.ledger.finish(
                self.run_id,
                operation,
                1,
                "FAILED",
                {"error": type(exc).__name__},
                False,
            )
            raise
        self.ledger.finish(self.run_id, operation, 1, "SUCCEEDED", {"status": "received"})
        return value

    def catalog(self, quantity):
        product = self.call(
            "catalog",
            "query($slug:String!,$channel:String!){product(slug:$slug,channel:$channel){variants{id name quantityAvailable}}}",
            {"slug": self.config.product_slug, "channel": self.config.channel},
            "product",
        )
        variants = [
            item
            for item in product["variants"]
            if item["name"] == self.config.variant_name
            and isinstance(item["quantityAvailable"], int)
            and item["quantityAvailable"] >= quantity
        ]
        if len(variants) != 1:
            raise ToolFailure("Expected one stocked configured variant")
        return variants[0]["id"]

    def create(self, variant, quantity):
        value = self.call(
            "create",
            "mutation($input:CheckoutCreateInput!){checkoutCreate(input:$input){errors{code message} checkout{id "
            + FIELDS
            + "}}}",
            {
                "input": {
                    "channel": self.config.channel,
                    "lines": [{"variantId": variant, "quantity": quantity}],
                }
            },
            "checkoutCreate",
        )
        if value.get("errors") or not value.get("checkout"):
            raise ToolFailure("Saleor rejected the behavior fixture")
        return value["checkout"]

    def observe(self, checkout_id, operation="observe"):
        return self.call(
            operation,
            "query($id:ID!){checkout(id:$id){id " + FIELDS + "}}",
            {"id": checkout_id},
            "checkout",
        )

    def promo(self, checkout_id, code, operation):
        return self.call(
            operation,
            "mutation($id:ID!,$code:String!){checkoutAddPromoCode(id:$id,promoCode:$code){errors{code message} checkout{id "
            + FIELDS
            + "}}}",
            {"id": checkout_id, "code": code},
            "checkoutAddPromoCode",
        )

    def run(self):
        scenario = self.config.scenario
        quantity = scenario.initial_quantity if scenario.scenario == "quantity_change" else 1
        target = scenario.target_quantity if scenario.scenario == "quantity_change" else quantity
        variant = self.catalog(max(quantity, target))
        checkout = self.create(variant, quantity)
        checkout_id = checkout["id"]
        before = public(checkout)
        if before["channel"] != self.config.channel or before["total"]["currency"] != self.config.currency:
            raise ToolFailure("Checkout fixture is outside the configured channel or currency")
        if scenario.scenario == "invalid_voucher":
            rejected = self.promo(checkout_id, scenario.invalid_code, "invalid-promo")
            after = public(self.observe(checkout_id, "observe-after-invalid"))
            passed = bool(rejected.get("errors") and rejected.get("checkout") is None and after == before)
            checks = {
                "invalid_code_rejected": bool(rejected.get("errors")),
                "checkout_unchanged": after == before,
            }
        elif scenario.scenario == "quantity_change":
            applied = self.promo(checkout_id, scenario.voucher_code, "apply-promo")
            if applied.get("errors") or not applied.get("checkout"):
                raise ToolFailure("Configured voucher was not eligible")
            applied_public = public(applied["checkout"])
            updated = self.call(
                "update-lines",
                "mutation($id:ID!,$lines:[CheckoutLineUpdateInput!]!){checkoutLinesUpdate(id:$id,lines:$lines){errors{code message} checkout{id "
                + FIELDS
                + "}}}",
                {
                    "id": checkout_id,
                    "lines": [
                        {
                            "lineId": checkout["lines"][0]["id"],
                            "quantity": scenario.target_quantity,
                        }
                    ],
                },
                "checkoutLinesUpdate",
            )
            after = (
                public(updated["checkout"]) if not updated.get("errors") and updated.get("checkout") else None
            )
            checks = {
                "quantity_updated": bool(after and after["lines"][0]["quantity"] == scenario.target_quantity),
                "voucher_remained_active": bool(after and after["voucher"] == scenario.voucher_code),
                "total_recalculated": bool(
                    after
                    and Decimal(str(after["total"]["amount"]))
                    != Decimal(str(applied_public["total"]["amount"]))
                ),
            }
            passed = all(checks.values())
        else:
            rejected = self.promo(checkout_id, scenario.voucher_code, "promo-before-shipping")
            address = scenario.address
            address_value = {
                "firstName": address.first_name,
                "lastName": address.last_name,
                "streetAddress1": address.street_address_1,
                "city": address.city,
                "postalCode": address.postal_code,
                "country": address.country,
            }
            changed = self.call(
                "set-address",
                "mutation($id:ID!,$address:AddressInput!){checkoutShippingAddressUpdate(id:$id,shippingAddress:$address){errors{code message} checkout{id shippingMethods{id name} "
                + FIELDS
                + "}}}",
                {"id": checkout_id, "address": address_value},
                "checkoutShippingAddressUpdate",
            )
            if changed.get("errors") or not changed.get("checkout"):
                raise ToolFailure("Shipping address was rejected")
            methods = [
                method
                for method in changed["checkout"].get("shippingMethods", [])
                if method["name"] == scenario.shipping_method_name
            ]
            if len(methods) != 1:
                raise ToolFailure("Configured shipping method is unavailable")
            selected = self.call(
                "set-delivery",
                "mutation($id:ID!,$method:ID!){checkoutDeliveryMethodUpdate(id:$id,deliveryMethodId:$method){errors{code message} checkout{id "
                + FIELDS
                + "}}}",
                {"id": checkout_id, "method": methods[0]["id"]},
                "checkoutDeliveryMethodUpdate",
            )
            if selected.get("errors") or not selected.get("checkout"):
                raise ToolFailure("Shipping method was rejected")
            applied = self.promo(checkout_id, scenario.voucher_code, "promo-after-shipping")
            after = (
                public(applied["checkout"]) if not applied.get("errors") and applied.get("checkout") else None
            )
            checks = {
                "voucher_rejected_before_prerequisites": bool(rejected.get("errors")),
                "shipping_prerequisites_set": bool(
                    after
                    and after["shipping_country"] == address.country
                    and after["delivery_method"] == scenario.shipping_method_name
                ),
                "shipping_voucher_applied": bool(
                    after
                    and after["voucher"] == scenario.voucher_code
                    and Decimal(str(after["shipping"]["gross"]["amount"])) == 0
                ),
            }
            passed = all(checks.values())
        return {
            "schema_version": 1,
            "scenario": scenario.scenario,
            "status": "PASSED" if passed else "FAILED",
            "checks": checks,
            "before": before,
            "after": after,
            "tool_usage": self.ledger.usage(self.run_id),
            "safety": {"order_submitted": False, "payment_submitted": False, "automatic_retries": 0},
        }


def run_additional_behavior(config_path, run_id, *, client=None):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", run_id):
        raise ValueError("Use a safe run ID")
    config = load_additional_behavior(config_path)
    root = Path(config.state_directory)
    root.mkdir(parents=True, exist_ok=True)
    runner = SaleorScenarioRunner(config, root, run_id, client=client)
    fingerprint = digest(config.model_dump(mode="json"))
    with FileLock(str(root / f"{run_id}.lock"), timeout=0):
        runner.ledger.register(run_id, fingerprint)
        if runner.ledger.usage(run_id):
            runner.close()
            raise UncertainExecution("Behavior scenario has prior attempts; use a new run ID")
        try:
            result = runner.run()
        finally:
            runner.close()
        saved = save_artifact(root, run_id, canonical(result).encode(), ".behavior.json")
        result["artifact"] = saved
        return result


def additional_behavior_schema():
    return {
        **AdditionalBehaviorConfig.model_json_schema(),
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Additional Saleor behavior scenario",
    }
