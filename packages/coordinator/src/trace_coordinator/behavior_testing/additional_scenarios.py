"""Bounded Saleor API oracles for additional approved behavior scenarios."""

import re
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

import httpx
from filelock import FileLock
from pydantic import Field, model_validator

from trace_coordinator.config import CallLimits
from trace_coordinator.domain.contracts import JsonObject, as_json_object, as_json_value
from trace_coordinator.domain.errors import ToolFailure, UncertainExecution
from trace_coordinator.domain.models import Record
from trace_coordinator.domain.project import Deployment
from trace_coordinator.storage.artifacts import save_artifact
from trace_coordinator.storage.call_ledger import CallLedger, canonical, digest

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
    def changed_quantity(self) -> Self:
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
    def safe(self) -> Self:
        parts = urlsplit(self.graphql_url)
        Deployment.public_url(f"{parts.scheme}://{parts.netloc}")
        if parts.query or parts.fragment or parts.username or parts.password:
            raise ValueError("Use a public GraphQL URL without credentials")
        if self.limits.retry_attempts != 1:
            raise ValueError("Behavior mutations must not retry automatically")
        return self


def load_additional_behavior(path: str | Path) -> AdditionalBehaviorConfig:
    path = Path(path).resolve()
    config = AdditionalBehaviorConfig.model_validate_json(path.read_text(encoding="utf-8-sig"))
    return config.model_copy(
        update={"state_directory": str((path.parent / config.state_directory).resolve())}
    )


def _objects(value: object, *, field: str) -> list[JsonObject]:
    if not isinstance(value, list):
        raise ToolFailure(f"Saleor field {field} was not a list")
    return [as_json_object(item) for item in value]


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise ToolFailure(f"Saleor field {field} was not text")
    return value


def public(checkout: JsonObject) -> JsonObject:
    total = as_json_object(as_json_object(checkout["totalPrice"])["gross"])
    channel = as_json_object(checkout["channel"])
    shipping_address = as_json_object(checkout.get("shippingAddress") or {})
    country = as_json_object(shipping_address.get("country") or {})
    delivery = as_json_object(checkout.get("delivery") or {})
    shipping_method = as_json_object(delivery.get("shippingMethod") or {})
    lines = _objects(checkout["lines"], field="lines")
    return as_json_object(
        {
            "voucher": checkout.get("voucherCode"),
            "discount": checkout["discount"],
            "shipping": checkout.get("shippingPrice"),
            "total": total,
            "lines": sorted(
                [
                    {
                        "variant": _text(as_json_object(line["variant"])["id"], field="variant.id"),
                        "quantity": line["quantity"],
                    }
                    for line in lines
                ],
                key=lambda row: str(row["variant"]),
            ),
            "channel": channel["slug"],
            "shipping_country": country.get("code"),
            "delivery_method": shipping_method.get("name"),
        }
    )


class SaleorScenarioRunner:
    def __init__(
        self,
        config: AdditionalBehaviorConfig,
        root: str | Path,
        run_id: str,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.config, self.root, self.run_id = config, Path(root), run_id
        self.client = client or httpx.Client(timeout=config.timeout_seconds, follow_redirects=False)
        self.owns_client = client is None
        self.ledger = CallLedger(self.root / "additional-behavior.sqlite")

    def close(self) -> None:
        if self.owns_client:
            self.client.close()

    def call(self, operation: str, query: str, variables: JsonObject, field: str) -> JsonObject:
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
            payload = as_json_object(response.json())
            data_value = payload.get("data")
            if payload.get("errors") or not isinstance(data_value, dict):
                raise ToolFailure("Saleor behavior operation failed")
            data = as_json_object(data_value)
            if data.get(field) is None:
                raise ToolFailure("Saleor behavior operation returned an empty field")
            value = as_json_object(data[field])
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

    def catalog(self, quantity: int) -> str:
        product = self.call(
            "catalog",
            "query($slug:String!,$channel:String!){product(slug:$slug,channel:$channel){variants{id name quantityAvailable}}}",
            as_json_object({"slug": self.config.product_slug, "channel": self.config.channel}),
            "product",
        )
        variants = [
            item
            for item in _objects(product["variants"], field="variants")
            if item["name"] == self.config.variant_name
            and isinstance(item["quantityAvailable"], int)
            and item["quantityAvailable"] >= quantity
        ]
        if len(variants) != 1:
            raise ToolFailure("Expected one stocked configured variant")
        return _text(variants[0]["id"], field="variant.id")

    def create(self, variant: str, quantity: int) -> JsonObject:
        value = self.call(
            "create",
            "mutation($input:CheckoutCreateInput!){checkoutCreate(input:$input){errors{code message} checkout{id "
            + FIELDS
            + "}}}",
            as_json_object(
                {
                    "input": {
                        "channel": self.config.channel,
                        "lines": [{"variantId": variant, "quantity": quantity}],
                    }
                }
            ),
            "checkoutCreate",
        )
        if value.get("errors") or not value.get("checkout"):
            raise ToolFailure("Saleor rejected the behavior fixture")
        return as_json_object(value["checkout"])

    def observe(self, checkout_id: str, operation: str = "observe") -> JsonObject:
        return self.call(
            operation,
            "query($id:ID!){checkout(id:$id){id " + FIELDS + "}}",
            as_json_object({"id": checkout_id}),
            "checkout",
        )

    def promo(self, checkout_id: str, code: str, operation: str) -> JsonObject:
        return self.call(
            operation,
            "mutation($id:ID!,$code:String!){checkoutAddPromoCode(id:$id,promoCode:$code){errors{code message} checkout{id "
            + FIELDS
            + "}}}",
            as_json_object({"id": checkout_id, "code": code}),
            "checkoutAddPromoCode",
        )

    def run(self) -> JsonObject:
        scenario = self.config.scenario
        quantity = scenario.initial_quantity if scenario.scenario == "quantity_change" else 1
        target = scenario.target_quantity if scenario.scenario == "quantity_change" else quantity
        variant = self.catalog(max(quantity, target))
        checkout = self.create(variant, quantity)
        checkout_id = _text(checkout["id"], field="checkout.id")
        before = public(checkout)
        before_total = as_json_object(before["total"])
        if before["channel"] != self.config.channel or before_total["currency"] != self.config.currency:
            raise ToolFailure("Checkout fixture is outside the configured channel or currency")
        after: JsonObject | None
        checks: dict[str, bool]
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
            applied_public = public(as_json_object(applied["checkout"]))
            checkout_lines = _objects(checkout["lines"], field="checkout.lines")
            updated = self.call(
                "update-lines",
                "mutation($id:ID!,$lines:[CheckoutLineUpdateInput!]!){checkoutLinesUpdate(id:$id,lines:$lines){errors{code message} checkout{id "
                + FIELDS
                + "}}}",
                as_json_object(
                    {
                        "id": checkout_id,
                        "lines": [
                            {
                                "lineId": checkout_lines[0]["id"],
                                "quantity": scenario.target_quantity,
                            }
                        ],
                    }
                ),
                "checkoutLinesUpdate",
            )
            after = (
                public(as_json_object(updated["checkout"]))
                if not updated.get("errors") and updated.get("checkout")
                else None
            )
            after_lines = _objects(after["lines"], field="after.lines") if after else []
            after_total = as_json_object(after["total"]) if after else {}
            applied_total = as_json_object(applied_public["total"])
            checks = {
                "quantity_updated": bool(
                    after_lines and after_lines[0]["quantity"] == scenario.target_quantity
                ),
                "voucher_remained_active": bool(after and after["voucher"] == scenario.voucher_code),
                "total_recalculated": bool(
                    after and Decimal(str(after_total["amount"])) != Decimal(str(applied_total["amount"]))
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
                as_json_object({"id": checkout_id, "address": address_value}),
                "checkoutShippingAddressUpdate",
            )
            if changed.get("errors") or not changed.get("checkout"):
                raise ToolFailure("Shipping address was rejected")
            changed_checkout = as_json_object(changed["checkout"])
            methods = [
                method
                for method in _objects(changed_checkout.get("shippingMethods", []), field="shippingMethods")
                if method["name"] == scenario.shipping_method_name
            ]
            if len(methods) != 1:
                raise ToolFailure("Configured shipping method is unavailable")
            selected = self.call(
                "set-delivery",
                "mutation($id:ID!,$method:ID!){checkoutDeliveryMethodUpdate(id:$id,deliveryMethodId:$method){errors{code message} checkout{id "
                + FIELDS
                + "}}}",
                as_json_object({"id": checkout_id, "method": methods[0]["id"]}),
                "checkoutDeliveryMethodUpdate",
            )
            if selected.get("errors") or not selected.get("checkout"):
                raise ToolFailure("Shipping method was rejected")
            applied = self.promo(checkout_id, scenario.voucher_code, "promo-after-shipping")
            after = (
                public(as_json_object(applied["checkout"]))
                if not applied.get("errors") and applied.get("checkout")
                else None
            )
            after_shipping = as_json_object(after["shipping"]) if after else {}
            shipping_gross = as_json_object(after_shipping.get("gross") or {})
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
                    and Decimal(str(shipping_gross["amount"])) == 0
                ),
            }
            passed = all(checks.values())
        return as_json_object(
            {
                "schema_version": 1,
                "scenario": scenario.scenario,
                "status": "PASSED" if passed else "FAILED",
                "checks": as_json_object(checks),
                "before": before,
                "after": after,
                "tool_usage": as_json_value(self.ledger.usage(self.run_id)),
                "safety": {"order_submitted": False, "payment_submitted": False, "automatic_retries": 0},
            }
        )


def run_additional_behavior(
    config_path: str | Path, run_id: str, *, client: httpx.Client | None = None
) -> JsonObject:
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
        result["artifact"] = as_json_value(saved)
        return result


def additional_behavior_schema() -> JsonObject:
    return as_json_object(
        {
            **AdditionalBehaviorConfig.model_json_schema(),
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Additional Saleor behavior scenario",
        }
    )
