import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import jsonschema
import pytest
from pydantic import ValidationError

from trace_coordinator.application.verification import (
    VoucherVerificationConfig,
    VoucherVerifier,
    run_verification,
    validate_oracle,
    verification_markdown,
)
from trace_coordinator.config import CallLimits
from trace_coordinator.domain.errors import RunMismatch, ToolFailure, UncertainExecution
from trace_coordinator.domain.models import Evidence, ToolContext, ToolResult
from trace_coordinator.domain.project import ApplicationConfig
from trace_coordinator.infrastructure.adapters.voucher_verification import (
    EmptyInput,
    FixtureInput,
    FixtureTool,
    ProbeInput,
    SaleorFixtures,
)
from trace_coordinator.infrastructure.ledger import CallLedger

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def inputs(tmp_path):
    app = ApplicationConfig(
        project_id="p",
        repository="o/r",
        repository_path=str(tmp_path),
        graph_snapshot_file="graph",
        ingestion_run_directory="corpus",
        retrieval_config_file="retrieval",
        vector_directory="vector",
        baseline={"url": "https://baseline.example", "revision": "a" * 40},
        patched={"url": "https://patched.example", "revision": "b" * 40},
    )
    config = VoucherVerificationConfig(
        application_file=str(tmp_path / "application.json"),
        state_directory=str(tmp_path / "runs"),
        graphql_url="https://api.example/graphql/",
        channel="default-channel",
        product_slug="tee",
        variant_name="S",
        voucher_code="TEST10",
        discount_percent="10",
        total_selector="data.total",
        assertion_timeout_seconds=1,
    )
    return config, app


def state(applied=False):
    return dict(
        voucher="TEST10" if applied else None,
        total="14.4" if applied else "16",
        discount="1.6" if applied else "0",
        currency="USD",
        lines=[{"variant": "v", "quantity": 1}],
        channel="default-channel",
        shipping_country=None,
    )


class World:
    def __init__(self, config, *, bad_fixture=False, missing_control=False, patched_fails=False):
        self.config = config
        self.applied = {"baseline": False, "patched": False}
        self.filled = {"baseline": False, "patched": False}
        self.counter = 0
        self.bad_fixture, self.missing_control, self.patched_fails = (
            bad_fixture,
            missing_control,
            patched_fails,
        )

    def evidence(self, kind, data):
        self.counter += 1
        return Evidence(
            id=f"{kind}-{self.counter}",
            kind=kind,
            project_id="p",
            source="fixture:test",
            summary=json.dumps(data),
        )

    def capture(self, environment):
        elements = (
            [dict(id="remove", name="Remove discount code", disabled=False)]
            if self.applied[environment]
            else [
                dict(id="field", name="Discount code", disabled=False),
                dict(id="apply", name="Apply", disabled=not self.filled[environment]),
            ]
        )
        if self.missing_control:
            elements = []
        return self.evidence("browser", dict(elements=elements))

    def tools(self):
        from trace_coordinator.infrastructure.adapters.browser import ActionInput, NavigateInput

        world = self

        class Tool:
            allowed_agents = frozenset({"coordinator"})

            def __init__(self, name, input_model):
                self.name, self.input_model = name, input_model

            def execute(self, args, context):
                name = self.name
                if name == "fixture.catalog":
                    data = {}
                elif name == "fixture.prepare":
                    data = state()
                elif name == "fixture.apply":
                    data = state(True)
                    data["total"] = "99" if world.bad_fixture else data["total"]
                elif name == "fixture.observe":
                    data = state(world.applied[args.environment])
                elif name == "browser.navigate":
                    return ToolResult(evidence=(world.capture(args.environment),))
                elif name == "browser.act":
                    if args.action == "fill":
                        world.filled[args.environment] = True
                    elif args.element_id == "apply":
                        world.applied[args.environment] = (
                            args.environment == "patched" and not world.patched_fails
                        )
                    else:
                        world.applied[args.environment] = False
                        world.filled[args.environment] = False
                    return ToolResult(evidence=(world.capture(args.environment),))
                else:
                    captured = world.capture(args.environment)
                    meets = (
                        world.applied[args.environment]
                        if args.mode == "applied"
                        else not world.applied[args.environment]
                    )
                    check = world.evidence(
                        "verification", dict(ready=True, conditions_met=meets, capture_id=captured.id)
                    )
                    return ToolResult(evidence=(captured, check))
                return ToolResult(evidence=(world.evidence("fixture", data),))

        return [
            Tool("fixture.catalog", EmptyInput),
            *[Tool(n, FixtureInput) for n in ("fixture.prepare", "fixture.apply", "fixture.observe")],
            Tool("browser.navigate", NavigateInput),
            Tool("browser.act", ActionInput),
            Tool("browser.check", ProbeInput),
        ]


def execute(inputs, tmp_path, **kwargs):
    config, app = inputs
    ledger = CallLedger(tmp_path / "ledger.sqlite")
    ledger.register("run", "fingerprint")
    result = VoucherVerifier(config, app, ledger, World(config, **kwargs).tools()).run("run")
    return result, ledger


def test_observed_failure_and_pass_are_separate_from_causal_pr_claim(inputs, tmp_path):
    result, ledger = execute(inputs, tmp_path)
    assert result["status"] == "COMPLETED"
    assert [c["status"] for c in result["checks"]] == ["PASS", "FAIL", "FAIL", "PASS", "PASS", "PASS", "PASS"]
    assert result["comparison"]["status"] == "INCONCLUSIVE"
    usage = {r["tool"]: r["attempts"] for r in ledger.usage("run")}
    assert usage["browser.act"] == usage["browser.check"] == 5
    assert sum(usage.values()) == 20 and all(v <= 5 for v in usage.values())
    assert all(ref in result["evidence"] for c in result["checks"] for ref in c["evidence_ids"])
    assert "No payment" in verification_markdown(result)


@pytest.mark.parametrize("option", ["bad_fixture", "missing_control", "patched_fails"])
def test_missing_prerequisites_never_become_passes(inputs, tmp_path, option):
    result, _ = execute(inputs, tmp_path, **{option: True})
    if option == "patched_fails":
        assert result["checks"][-1]["status"] == "BLOCKED"
    else:
        assert result["status"] == "BLOCKED"
        assert any(c["status"] == "NOT_RUN" for c in result["checks"])
        assert "Stopped:" in verification_markdown(result)


def test_lower_limit_stops_before_sixth_action_without_reset(inputs, tmp_path):
    config, app = inputs
    config = config.model_copy(update={"limits": CallLimits(per_agent_tool=4, retry_attempts=1)})
    result, ledger = execute((config, app), tmp_path)
    assert result["status"] == "BLOCKED"
    assert "limit" in result["stop_reason"]
    assert all(row["attempts"] <= 4 for row in ledger.usage("run"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("graphql_url", "https://user:secret@api.example/graphql/"),
        ("graphql_url", "ftp://api.example/graphql/"),
        ("graphql_url", "https://api.example/graphql/?key=x"),
        ("checkout_path", "//evil.example"),
        ("checkout_path", "/checkout?checkout=private"),
        ("limits", {"retry_attempts": 2}),
    ],
)
def test_bad_config_rejected(inputs, field, value):
    with pytest.raises(ValidationError):
        VoucherVerificationConfig.model_validate({**inputs[0].model_dump(), field: value})


def test_oracle_rejects_wrong_percentage_currency_and_line_set(inputs):
    config, _ = inputs
    assert validate_oracle(state(), state(True), config)
    for key, value in [("currency", "EUR"), ("discount", "0"), ("voucher", "OTHER"), ("lines", [])]:
        assert not validate_oracle(state(), {**state(True), key: value}, config)


def test_saved_run_replays_report_not_side_effects_and_rejects_changed_config(inputs, tmp_path, monkeypatch):
    from trace_coordinator.infrastructure.adapters import voucher_verification

    config, app = inputs
    Path(tmp_path / "graph-config.json").write_text(
        json.dumps({"provider": "neo4j", "baseline_snapshot_file": app.graph_snapshot_file})
    )
    Path(tmp_path / "retrieval-config.json").write_text(
        json.dumps(
            {
                "provider": "qdrant",
                "ingestion_run_directory": app.ingestion_run_directory,
                "vector_directory": app.vector_directory,
            }
        )
    )
    Path(tmp_path / "ui-config.json").write_text('{"provider":"disabled"}')
    Path(config.application_file).write_text(
        json.dumps(
            {
                "project_id": app.project_id,
                "repository": app.repository,
                "repository_path": app.repository_path,
                "change_source": app.change_source.model_dump(mode="json"),
                "graph_config_file": "graph-config.json",
                "retrieval_config_file": "retrieval-config.json",
                "ui_config_file": "ui-config.json",
                "baseline": {"url": app.baseline.url, "revision": app.baseline.revision},
                "patched": {"url": app.patched.url, "revision": app.patched.revision},
                "require_exact_revisions": False,
            }
        )
    )
    path = tmp_path / "config.json"
    path.write_text(config.model_dump_json())
    calls = []

    @contextmanager
    def tools(*args):
        calls.append(1)
        yield World(config).tools()

    monkeypatch.setattr(voucher_verification, "verification_tools", tools)
    first = run_verification(path, "test")
    assert run_verification(path, "test") == first
    assert len(calls) == 1
    changed = config.model_copy(update={"quantity": 2})
    path.write_text(changed.model_dump_json())
    with pytest.raises(RunMismatch):
        run_verification(path, "test")
    path.write_text(config.model_dump_json())
    (Path(config.state_directory) / "test.json").unlink()
    with pytest.raises(UncertainExecution):
        run_verification(path, "test")
    assert len(calls) == 1
    with pytest.raises(ValueError):
        run_verification(path, "../bad")


def checkout(applied=False):
    s = state(applied)
    return dict(
        id="PRIVATE-CHECKOUT-TOKEN",
        voucherCode=s["voucher"],
        discount=dict(amount=s["discount"], currency="USD"),
        totalPrice={"gross": {"amount": s["total"], "currency": "USD"}},
        lines=[dict(quantity=1, variant={"id": "v"})],
        channel={"slug": "default-channel"},
        shippingAddress=None,
    )


def test_api_provider_never_logs_guest_tokens_or_mutates_ui_carts(inputs, tmp_path):
    config, app = inputs
    session = SimpleNamespace(app=app)

    def handler(request):
        q = json.loads(request.content)["query"]
        if "product(" in q:
            data = {"product": dict(slug="tee", variants=[dict(id="v", name="S", quantityAvailable=10)])}
        elif "checkoutCreate" in q:
            data = {"checkoutCreate": dict(errors=[], checkout=checkout())}
        elif "checkoutAddPromoCode" in q:
            data = {"checkoutAddPromoCode": dict(errors=[], checkout=checkout(True))}
        else:
            data = {"checkout": checkout()}
        return httpx.Response(200, json={"data": data})

    ctx = ToolContext(run_id="api", project_id="p", agent_id="coordinator")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = SaleorFixtures(config, app, session, tmp_path, client)
        results = [FixtureTool("fixture.catalog", provider).execute(EmptyInput(), ctx)]
        for environment in ("control", "baseline"):
            results.append(provider.execute("fixture.prepare", FixtureInput(environment=environment), ctx))
        results.append(provider.execute("fixture.apply", FixtureInput(environment="control"), ctx))
        results.append(provider.execute("fixture.observe", FixtureInput(environment="baseline"), ctx))
        assert "PRIVATE-CHECKOUT-TOKEN" in session.app.baseline.entry_path
        assert "PRIVATE-CHECKOUT-TOKEN" not in json.dumps([r.model_dump(mode="json") for r in results])
        for op, env in [
            ("fixture.prepare", "baseline"),
            ("fixture.apply", "baseline"),
            ("fixture.observe", "patched"),
        ]:
            with pytest.raises(ToolFailure):
                provider.execute(op, FixtureInput(environment=env), ctx)
        with pytest.raises(ToolFailure):
            provider.execute("fixture.catalog", EmptyInput(), ctx.model_copy(update={"project_id": "wrong"}))


@pytest.mark.parametrize(
    "payload",
    [
        {"errors": [{"message": "PRIVATE"}]},
        {"data": {"product": None}},
        {"data": {"product": {"errors": [{"code": "INVALID"}]}}},
        {"data": {"product": {"slug": "tee", "variants": []}}},
    ],
)
def test_api_errors_and_missing_stock_fail_closed(inputs, tmp_path, payload):
    config, app = inputs
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))) as client:
        provider = SaleorFixtures(config, app, SimpleNamespace(app=app), tmp_path, client)
        with pytest.raises(ToolFailure):
            provider.execute(
                "fixture.catalog",
                EmptyInput(),
                ToolContext(run_id="api", project_id="p", agent_id="coordinator"),
            )


def test_json_schema_is_current():
    schema = json.loads((ROOT / "schemas/voucher-verification.schema.json").read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(json.loads((ROOT / "configs/verification/saleor-voucher.json").read_text()), schema)
    assert {
        k: v for k, v in schema.items() if k != "$schema"
    } == VoucherVerificationConfig.model_json_schema()


def test_verification_cli(inputs, tmp_path, monkeypatch, capsys):
    from trace_coordinator.application import verification
    from trace_coordinator.presentation.cli import main

    result, _ = execute(inputs, tmp_path)
    monkeypatch.setattr(verification, "run_verification", lambda *_: result)
    monkeypatch.setattr(
        "sys.argv",
        [
            "trace-coordinator",
            "verify-voucher",
            "config.json",
            "--run-id",
            "test",
            "--output",
            str(tmp_path / "report"),
        ],
    )
    main()
    assert "COMPLETED" in capsys.readouterr().out
    assert (tmp_path / "report/report.md").exists()
