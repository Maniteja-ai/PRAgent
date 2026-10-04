import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from trace_coordinator.domain.errors import ToolFailure
from trace_coordinator.domain.models import ToolContext
from trace_coordinator.domain.project import ApplicationConfig
from trace_coordinator.tool.implementations.browser import (
    ActionInput,
    BrowserSession,
    NavigateInput,
    ObserveInput,
)

pytestmark = pytest.mark.browser

PAGE = b"""<!DOCTYPE html><html><body><h1>Checkout</h1>
<label>Voucher code<input id="code" placeholder="Voucher code"></label>
<label>Shipping<select aria-label="Shipping"><option>Standard</option><option>Express</option></select></label>
<input aria-label="Private field">
<button data-impact-id="checkout.discount.apply" onclick="document.querySelector('#total').textContent='9'">Apply</button>
<button onclick="document.querySelector('#total').textContent='ORDER PLACED'">Place order</button>
<button data-testid="CartToggle" onclick="this.textContent='Cart opened'">1 item in cart</button>
<p id="total">10</p><a href="https://outside.example/">External</a>
<a href="/dynamic" onclick="event.preventDefault(); setTimeout(() => {
history.pushState({}, '', '/dynamic'); setTimeout(() => {
const e=document.createElement('button'); e.id='destination'; e.textContent='Destination ready';
document.body.appendChild(e);}, 150);}, 120)">Dynamic route</a>
<script>const privateToken='do-not-capture';</script>
<input type="hidden" value="secret-hidden"></body></html>"""


@pytest.fixture
def browser(tmp_path):
    pytest.importorskip("playwright")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(PAGE)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port
    app = ApplicationConfig(
        project_id="p",
        repository="owner/repo",
        repository_path=str(tmp_path),
        graph_snapshot_file="graph",
        ingestion_run_directory="run",
        retrieval_config_file="retrieval",
        vector_directory="vector",
        baseline={"url": f"http://127.0.0.1:{port}", "revision": "a" * 40},
        patched={"url": f"http://localhost:{port}", "revision": "b" * 40},
        browser={
            "allowed_button_names": ["Apply"],
            "allowed_field_names": ["Voucher code", "Shipping"],
            "settle_ms": 0,
        },
    )
    session = BrowserSession(app, tmp_path / "artifacts")
    try:
        yield session
    finally:
        session.close()
        server.shutdown()
        server.server_close()


def context():
    return ToolContext(run_id="run", project_id="p", agent_id="coordinator")


def navigate(browser, environment="baseline"):
    return browser.execute("browser.navigate", NavigateInput(environment=environment), context()).evidence[0]


def element(evidence, name):
    return next(e for e in json.loads(evidence.summary)["elements"] if e["name"] == name)


VERIFICATION_PAGE = """<html><body>
<input placeholder="Discount code" oninput="document.querySelector('#apply').disabled=!this.value">
<button id="apply" disabled onclick="setTimeout(()=>{
document.querySelector('input').style.display='none';this.style.display='none';
document.querySelector('#code').textContent='TEST10';document.querySelector('#remove').style.display='block';
document.querySelector('data').setAttribute('value','14.4');document.querySelector('data').textContent='$14.40';},150)">Apply</button>
<button id="remove" style="display:none" aria-label="Remove discount code" onclick="
this.style.display='none';document.querySelector('input').style.display='block';document.querySelector('input').value='';
document.querySelector('#apply').style.display='block';document.querySelector('#apply').disabled=true;
document.querySelector('#code').textContent='';document.querySelector('data').setAttribute('value','16');document.querySelector('data').textContent='$16.00';">Remove</button>
<p id="code"></p><data class="total" value="16">$16.00</data></body></html>"""


def test_voucher_probe_waits_for_ui_updates_and_preserves_fresh_action_handles(browser):
    from trace_coordinator.behavior_testing.voucher_test import VoucherVerificationConfig
    from trace_coordinator.tool.implementations.saleor_voucher_tools import ProbeInput, VoucherProbe

    navigate(browser)
    browser.app = browser.app.model_copy(
        update={
            "browser": browser.app.browser.model_copy(
                update={
                    "allowed_button_names": ("Apply", "Remove discount code"),
                    "allowed_field_names": ("Discount code",),
                }
            )
        }
    )
    browser.executor.submit(lambda: browser.pages["baseline"].set_content(VERIFICATION_PAGE)).result()
    config = VoucherVerificationConfig(
        application_file="app",
        state_directory="runs",
        graphql_url="https://api.example/graphql/",
        channel="channel",
        product_slug="tee",
        variant_name="S",
        voucher_code="TEST10",
        discount_percent="10",
        total_selector="data.total",
        assertion_timeout_seconds=1,
    )
    probe = VoucherProbe(browser, config)
    before = probe.execute(
        ProbeInput(environment="baseline", mode="initial", expected_total="16", voucher_code="TEST10"),
        context(),
    )
    assert json.loads(before.evidence[-1].summary)["conditions_met"]
    capture = before.evidence[0]
    for name, action, value in [("Discount code", "fill", "TEST10"), ("Apply", "click", "")]:
        capture = browser.execute(
            "browser.act",
            ActionInput(
                environment="baseline",
                snapshot_id=capture.id,
                element_id=element(capture, name)["id"],
                action=action,
                value=value,
            ),
            context(),
        ).evidence[0]
    applied = probe.execute(
        ProbeInput(environment="baseline", mode="applied", expected_total="14.4", voucher_code="TEST10"),
        context(),
    )
    assert json.loads(applied.evidence[-1].summary)["conditions_met"]
    capture = applied.evidence[0]
    browser.execute(
        "browser.act",
        ActionInput(
            environment="baseline",
            snapshot_id=capture.id,
            element_id=element(capture, "Remove discount code")["id"],
            action="click",
        ),
        context(),
    )
    removed = probe.execute(
        ProbeInput(environment="baseline", mode="removed", expected_total="16", voucher_code="TEST10"),
        context(),
    )
    assert json.loads(removed.evidence[-1].summary)["conditions_met"]
    wrong = probe.execute(
        ProbeInput(environment="baseline", mode="applied", expected_total="14.4", voucher_code="TEST10"),
        context(),
    )
    assert not json.loads(wrong.evidence[-1].summary)["conditions_met"]
    assert json.loads(wrong.evidence[-1].summary)["ready"]
    with pytest.raises(ToolFailure, match="No live browser"):
        probe.execute(
            ProbeInput(environment="baseline", mode="initial", expected_total="16", voucher_code="TEST10"),
            context().model_copy(update={"run_id": "other"}),
        )


@pytest.mark.parametrize(
    "total_markup",
    [
        '<data class="total" value="garbled"></data>',
        '<data class="total" value="16"></data><data class="total" value="16"></data>',
    ],
)
def test_voucher_probe_rejects_invalid_or_ambiguous_total(browser, total_markup):
    from trace_coordinator.behavior_testing.voucher_test import VoucherVerificationConfig
    from trace_coordinator.tool.implementations.saleor_voucher_tools import ProbeInput, VoucherProbe

    navigate(browser)
    html = VERIFICATION_PAGE.replace('<data class="total" value="16">$16.00</data>', total_markup)
    browser.executor.submit(lambda: browser.pages["baseline"].set_content(html)).result()
    config = VoucherVerificationConfig(
        application_file="app",
        state_directory="runs",
        graphql_url="https://api.example/graphql/",
        channel="channel",
        product_slug="tee",
        variant_name="S",
        voucher_code="TEST10",
        discount_percent="10",
        total_selector="data.total",
        assertion_timeout_seconds=1,
    )
    result = VoucherProbe(browser, config).execute(
        ProbeInput(environment="baseline", mode="initial", expected_total="16", voucher_code="TEST10"),
        context(),
    )
    assert not json.loads(result.evidence[-1].summary)["conditions_met"]


def test_live_local_capture_and_single_action(browser):
    first = navigate(browser)
    button = element(first, "Apply")
    result = browser.execute(
        "browser.act",
        ActionInput(environment="baseline", snapshot_id=first.id, element_id=button["id"], action="click"),
        context(),
    ).evidence[0]
    assert "9" in json.loads(result.summary)["text"]
    assert result.id != first.id
    assert Path(result.metadata["screenshot"]["path"]).is_file()
    transition = json.loads(Path(result.metadata["transition"]["path"]).read_text(encoding="utf-8"))
    assert transition["from"] == first.id and transition["to"] == result.id


def test_capture_preserves_stable_impact_id_in_json_and_sanitized_dom(browser):
    observed = navigate(browser)
    apply = element(observed, "Apply")
    assert apply["impact_id"] == "checkout.discount.apply"
    dom = Path(observed.metadata["dom"]["path"]).read_text(encoding="utf-8")
    assert 'data-impact-id="checkout.discount.apply"' in dom


def test_disallowed_button_and_external_link_do_not_execute(browser):
    first = navigate(browser)
    for name in ("Place order", "External"):
        with pytest.raises(ToolFailure):
            browser.execute(
                "browser.act",
                ActionInput(
                    environment="baseline",
                    snapshot_id=first.id,
                    element_id=element(first, name)["id"],
                    action="click",
                ),
                context(),
            )
    observed = browser.execute("browser.observe", ObserveInput(environment="baseline"), context()).evidence[0]
    assert "ORDER PLACED" not in json.loads(observed.summary)["text"]


def test_stale_snapshot_rejected(browser):
    first = navigate(browser)
    navigate(browser)
    with pytest.raises(ToolFailure, match="latest"):
        browser.execute(
            "browser.act",
            ActionInput(
                environment="baseline",
                snapshot_id=first.id,
                element_id=element(first, "Apply")["id"],
                action="click",
            ),
            context(),
        )


def test_external_navigation_rejected(browser):
    with pytest.raises(ToolFailure, match="within"):
        browser.execute(
            "browser.navigate", NavigateInput(environment="baseline", path="//outside.example"), context()
        )


def test_dom_excludes_script_hidden_fields_and_values(browser):
    first = navigate(browser)
    dom = Path(first.metadata["dom"]["path"]).read_text(encoding="utf-8")
    assert "do-not-capture" not in dom
    assert "secret-hidden" not in dom
    assert "<script" not in dom


def test_other_environment_snapshot_cannot_authorize_action(browser):
    first = navigate(browser, "baseline")
    navigate(browser, "patched")
    with pytest.raises(ToolFailure, match="latest"):
        browser.execute(
            "browser.act",
            ActionInput(
                environment="patched",
                snapshot_id=first.id,
                element_id=element(first, "Apply")["id"],
                action="click",
            ),
            context(),
        )


def test_lost_session_is_not_reconstructed_by_repeating_actions(browser):
    with pytest.raises(ToolFailure, match="session unavailable"):
        browser.execute("browser.observe", ObserveInput(environment="baseline"), context())


def test_fill_select_and_unapproved_field(browser):
    observed = navigate(browser)
    for name, action, value in [("Voucher code", "fill", "TEST10"), ("Shipping", "select", "Express")]:
        observed = browser.execute(
            "browser.act",
            ActionInput(
                environment="baseline",
                snapshot_id=observed.id,
                element_id=element(observed, name)["id"],
                action=action,
                value=value,
            ),
            context(),
        ).evidence[0]
    values = browser.executor.submit(
        lambda: (
            browser.pages["baseline"].locator("#code").input_value(),
            browser.pages["baseline"].locator("select").input_value(),
        )
    ).result()
    assert values == ("TEST10", "Express")
    with pytest.raises(ToolFailure, match="Field is not authorized"):
        browser.execute(
            "browser.act",
            ActionInput(
                environment="baseline",
                snapshot_id=observed.id,
                element_id=element(observed, "Private field")["id"],
                action="fill",
                value="secret",
            ),
            context(),
        )


def test_failed_capture_after_mutation_invalidates_old_authorization(browser, monkeypatch):
    observed = navigate(browser)
    original = browser._capture

    def fail(*args):
        raise ToolFailure("Capture failed after click")

    monkeypatch.setattr(browser, "_capture", fail)
    action = ActionInput(
        environment="baseline",
        snapshot_id=observed.id,
        element_id=element(observed, "Apply")["id"],
        action="click",
    )
    with pytest.raises(ToolFailure, match="after click"):
        browser.execute("browser.act", action, context())
    monkeypatch.setattr(browser, "_capture", original)
    with pytest.raises(ToolFailure, match="latest"):
        browser.execute("browser.act", action, context())
    fresh = browser.execute("browser.observe", ObserveInput(environment="baseline"), context()).evidence[0]
    assert fresh.id != observed.id
    assert "9" in json.loads(fresh.summary)["text"]


def test_changed_element_and_wrong_project_are_rejected(browser):
    observed = navigate(browser)
    browser.executor.submit(
        lambda: (
            browser.pages["baseline"]
            .get_by_role("button", name="Apply", exact=True)
            .evaluate("el => el.textContent='Place order'")
        )
    ).result()
    with pytest.raises(ToolFailure, match="changed since observation"):
        browser.execute(
            "browser.act",
            ActionInput(
                environment="baseline",
                snapshot_id=observed.id,
                element_id=element(observed, "Apply")["id"],
                action="click",
            ),
            context(),
        )
    with pytest.raises(ToolFailure, match="outside the project"):
        browser.execute(
            "browser.observe",
            ObserveInput(environment="baseline"),
            context().model_copy(update={"project_id": "other"}),
        )


def test_dom_budget_fails_instead_of_saving_truncated_evidence(browser):
    navigate(browser)
    browser.app = browser.app.model_copy(
        update={"browser": browser.app.browser.model_copy(update={"max_dom_chars": 1})}
    )
    with pytest.raises(ToolFailure, match="DOM exceeds"):
        browser.execute("browser.observe", ObserveInput(environment="baseline"), context())


def test_unobserved_route_cannot_be_guessed(browser):
    navigate(browser)
    with pytest.raises(ToolFailure, match="not observed"):
        browser.execute(
            "browser.navigate", NavigateInput(environment="baseline", path="/guessed-checkout"), context()
        )


def test_same_screen_has_stable_fingerprint_despite_capture_time(browser):
    first = navigate(browser)
    second = browser.execute("browser.observe", ObserveInput(environment="baseline"), context()).evidence[0]
    assert first.id != second.id
    assert json.loads(first.summary)["state_fingerprint"] == json.loads(second.summary)["state_fingerprint"]
    assert second.metadata["transition_record"]["from"] == first.id


def test_configured_readiness_waits_for_dynamic_control(browser):
    navigate(browser)
    browser.app = browser.app.model_copy(
        update={
            "browser": browser.app.browser.model_copy(
                update={"readiness_checks": {"/": "#late-control"}, "settle_ms": 0}
            )
        }
    )
    browser.executor.submit(
        lambda: browser.pages["baseline"].evaluate("""() => {
        setTimeout(() => {let e=document.createElement('button'); e.id='late-control';
        e.textContent='Ready'; document.body.appendChild(e);}, 300);
    }""")
    ).result()
    result = browser.execute("browser.observe", ObserveInput(environment="baseline"), context()).evidence[0]
    assert element(result, "Ready")


def test_client_side_navigation_uses_destination_readiness(browser):
    first = navigate(browser)
    browser.app = browser.app.model_copy(
        update={
            "browser": browser.app.browser.model_copy(
                update={"readiness_checks": {"/dynamic": "#destination"}, "settle_ms": 0}
            )
        }
    )
    result = browser.execute(
        "browser.act",
        ActionInput(
            environment="baseline",
            snapshot_id=first.id,
            element_id=element(first, "Dynamic route")["id"],
            action="click",
        ),
        context(),
    ).evidence[0]
    assert json.loads(result.summary)["url"].endswith("/dynamic")
    assert element(result, "Destination ready")


def test_observed_test_id_allows_dynamic_button_name(browser):
    first = navigate(browser)
    browser.app = browser.app.model_copy(
        update={
            "browser": browser.app.browser.model_copy(update={"allowed_button_test_ids": ("CartToggle",)})
        }
    )
    result = browser.execute(
        "browser.act",
        ActionInput(
            environment="baseline",
            snapshot_id=first.id,
            element_id=element(first, "1 item in cart")["id"],
            action="click",
        ),
        context(),
    ).evidence[0]
    assert element(result, "Cart opened")["test_id"] == "CartToggle"
