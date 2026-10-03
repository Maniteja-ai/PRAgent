import json
import sqlite3

import httpx
import pytest

from trace_coordinator.behavior_testing.additional_scenarios import (
    AdditionalBehaviorConfig,
    additional_behavior_schema,
    run_additional_behavior,
)


def checkout(*, voucher=None, quantity=1, total="16.00", country=None, method=None, shipping="4.00"):
    return {
        "id": "checkout-secret",
        "voucherCode": voucher,
        "discount": {"amount": "1.60" if voucher else "0.00", "currency": "USD"},
        "shippingPrice": {"gross": {"amount": shipping, "currency": "USD"}},
        "totalPrice": {"gross": {"amount": total, "currency": "USD"}},
        "lines": [{"id": "line-secret", "quantity": quantity, "variant": {"id": "v1"}}],
        "channel": {"slug": "default-channel"},
        "shippingAddress": {"country": {"code": country}} if country else None,
        "delivery": {"shippingMethod": {"id": "ship-1", "name": method}} if method else None,
    }


def config(tmp_path, scenario):
    path = tmp_path / "scenario.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "graphql_url": "https://api.example/graphql/",
                "channel": "default-channel",
                "product_slug": "tee",
                "variant_name": "S",
                "currency": "USD",
                "state_directory": "state",
                "scenario": scenario,
                "limits": {
                    "per_agent_tool": 5,
                    "total_calls": 20,
                    "retry_attempts": 1,
                    "max_run_seconds": 300,
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def client_for(scenario):
    promo_calls = 0

    def handler(request):
        nonlocal promo_calls
        body = json.loads(request.content)
        query, variables = body["query"], body["variables"]
        if "query($slug" in query:
            value = {"product": {"variants": [{"id": "v1", "name": "S", "quantityAvailable": 5}]}}
        elif "checkoutCreate" in query:
            value = {"checkoutCreate": {"errors": [], "checkout": checkout()}}
        elif "checkoutAddPromoCode" in query:
            promo_calls += 1
            if scenario == "invalid" or (scenario == "shipping" and promo_calls == 1):
                result = {"errors": [{"code": "INVALID", "message": "rejected"}], "checkout": None}
            elif scenario == "quantity":
                result = {"errors": [], "checkout": checkout(voucher="TSIGMA10", total="14.40")}
            else:
                result = {
                    "errors": [],
                    "checkout": checkout(voucher="TSIGMASHIP", country="PL", method="UPS", shipping="0.00"),
                }
            value = {"checkoutAddPromoCode": result}
        elif "checkoutLinesUpdate" in query:
            assert variables["lines"] == [{"lineId": "line-secret", "quantity": 2}]
            value = {
                "checkoutLinesUpdate": {
                    "errors": [],
                    "checkout": checkout(voucher="TSIGMA10", quantity=2, total="28.80"),
                }
            }
        elif "checkoutShippingAddressUpdate" in query:
            updated = checkout(country="PL")
            updated["shippingMethods"] = [{"id": "ship-1", "name": "UPS"}]
            value = {"checkoutShippingAddressUpdate": {"errors": [], "checkout": updated}}
        elif "checkoutDeliveryMethodUpdate" in query:
            value = {
                "checkoutDeliveryMethodUpdate": {
                    "errors": [],
                    "checkout": checkout(country="PL", method="UPS"),
                }
            }
        elif "query($id" in query:
            value = {"checkout": checkout()}
        else:
            raise AssertionError(query)
        return httpx.Response(200, json={"data": value})

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("name", "scenario", "expected_checks"),
    [
        (
            "invalid",
            {"scenario": "invalid_voucher", "invalid_code": "NEVER-VALID"},
            {"invalid_code_rejected": True, "checkout_unchanged": True},
        ),
        (
            "quantity",
            {
                "scenario": "quantity_change",
                "voucher_code": "TSIGMA10",
                "initial_quantity": 1,
                "target_quantity": 2,
            },
            {
                "quantity_updated": True,
                "voucher_remained_active": True,
                "total_recalculated": True,
            },
        ),
        (
            "shipping",
            {
                "scenario": "shipping_prerequisite",
                "voucher_code": "TSIGMASHIP",
                "shipping_method_name": "UPS",
                "address": {
                    "first_name": "Test",
                    "last_name": "Shopper",
                    "street_address_1": "1 Test Street",
                    "city": "Warsaw",
                    "postal_code": "00-001",
                    "country": "PL",
                },
            },
            {
                "voucher_rejected_before_prerequisites": True,
                "shipping_prerequisites_set": True,
                "shipping_voucher_applied": True,
            },
        ),
    ],
)
def test_behavior_oracles_are_bounded_and_sanitized(tmp_path, name, scenario, expected_checks):
    client = client_for(name)
    try:
        result = run_additional_behavior(config(tmp_path, scenario), f"test-{name}", client=client)
    finally:
        client.close()

    assert result["status"] == "PASSED"
    assert result["checks"] == expected_checks
    assert result["safety"] == {
        "order_submitted": False,
        "payment_submitted": False,
        "automatic_retries": 0,
    }
    database = tmp_path / "state" / "additional-behavior.sqlite"
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute("SELECT * FROM calls").fetchall()
    finally:
        connection.close()
    serialized = json.dumps(rows)
    assert "checkout-secret" not in serialized
    assert "1 Test Street" not in serialized
    assert "TSIGMA" not in serialized
    assert max(item["attempts"] for item in result["tool_usage"]) == 1


def test_behavior_config_disallows_mutation_retries(tmp_path):
    path = config(tmp_path, {"scenario": "invalid_voucher", "invalid_code": "NO"})
    data = json.loads(path.read_text(encoding="utf-8"))
    data["limits"]["retry_attempts"] = 2
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="must not retry"):
        run_additional_behavior(path, "unsafe")


def test_behavior_schema_and_config_reject_unsafe_inputs():
    assert additional_behavior_schema()["title"] == "Additional Saleor behavior scenario"
    base = {
        "graphql_url": "https://api.example/graphql/",
        "channel": "default-channel",
        "product_slug": "tee",
        "variant_name": "S",
        "state_directory": "state",
        "scenario": {
            "scenario": "quantity_change",
            "voucher_code": "SAVE",
            "initial_quantity": 2,
            "target_quantity": 2,
        },
    }
    with pytest.raises(ValueError, match="must differ"):
        AdditionalBehaviorConfig.model_validate(base)
    base["graphql_url"] = "https://user:secret@api.example/graphql/"
    base["scenario"] = {"scenario": "invalid_voucher", "invalid_code": "NO"}
    with pytest.raises(ValueError, match="without credentials"):
        AdditionalBehaviorConfig.model_validate(base)
