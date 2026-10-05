import hashlib
from pathlib import Path

import pytest

from impact_agent.config.loader.implementations.json_config_loader import JsonConfigLoader
from impact_agent.config.validation.behavior import (
    BehaviorActionConfig,
    BehaviorAssertionConfig,
    BehaviorConfig,
    BehaviorScenarioConfig,
)
from impact_agent.config.validation.browser import BrowserConfig
from impact_agent.domain.models import (
    ChangedFile,
    ConfirmedCodeUiMapping,
    Evidence,
    PullRequestRef,
    PullRequestSnapshot,
)
from impact_agent.tools.browser.implementations import playwright_adapters
from impact_agent.tools.browser.implementations.playwright_adapters import (
    PlaywrightAdapterError,
    PlaywrightAdapterFactory,
    PlaywrightBehaviorVerifier,
    PlaywrightBrowserExplorer,
)

CONFIG = Path(__file__).parents[1] / "config" / "default"


class FakeLocator:
    def __init__(self, selector: str) -> None:
        self.selector = selector
        self.actions: list[tuple[str, str | None]] = []

    def inner_text(self, **_kwargs: object) -> str:
        return "Saleor product page"

    def click(self, **_kwargs: object) -> None:
        self.actions.append(("click", None))

    def fill(self, value: str, **_kwargs: object) -> None:
        self.actions.append(("fill", value))

    def press(self, value: str, **_kwargs: object) -> None:
        self.actions.append(("press", value))

    def wait_for(self, **_kwargs: object) -> None:
        return None

    def input_value(self, **_kwargs: object) -> str:
        return "SAVE10"


class FakePage:
    def __init__(self) -> None:
        self.url = ""
        self.locators: dict[str, FakeLocator] = {}

    def set_default_timeout(self, _timeout: int) -> None:
        return None

    def goto(self, url: str, **_kwargs: object) -> None:
        self.url = url

    def title(self) -> str:
        return "Saleor Store"

    def locator(self, selector: str) -> FakeLocator:
        return self.locators.setdefault(selector, FakeLocator(selector))


class FakeBrowser:
    def __init__(self) -> None:
        self.page = FakePage()
        self.closed = False

    def new_page(self) -> FakePage:
        return self.page

    def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self) -> None:
        self.browser = FakeBrowser()

    def launch(self, **_kwargs: object) -> FakeBrowser:
        return self.browser


class FakePlaywright:
    def __init__(self) -> None:
        self.chromium = FakeChromium()

    def __enter__(self) -> "FakePlaywright":
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def test_browser_explorer_captures_hashed_text_from_configured_allowed_host(monkeypatch):
    settings = JsonConfigLoader().load(CONFIG)
    fake_playwright = FakePlaywright()
    monkeypatch.setattr(playwright_adapters, "sync_playwright", lambda: fake_playwright)
    explorer = PlaywrightBrowserExplorer(settings.browser)
    pull_request = PullRequestSnapshot(
        PullRequestRef("owner/storefront", 12),
        "Cart update",
        "",
        "base",
        "head",
        (ChangedFile("src/cart.ts", "modified", 1, 0),),
        "diff",
    )

    evidence = explorer.explore(pull_request)

    assert len(evidence) == len(settings.browser.start_urls)
    assert evidence[0].source.startswith("browser://testsigma-saleor-patched.vercel.app")
    assert "Saleor Store" in evidence[0].content
    assert evidence[0].content_sha256 == hashlib.sha256(evidence[0].content.encode()).hexdigest()
    assert fake_playwright.chromium.browser.closed


def test_browser_explorer_rejects_non_allowlisted_host_before_navigation(monkeypatch):
    settings = JsonConfigLoader().load(CONFIG)
    config = settings.browser.model_copy(update={"start_urls": ("https://attacker.example/",)})
    # The config validator is bypassed deliberately to verify the adapter's runtime boundary too.
    fake_playwright = FakePlaywright()
    monkeypatch.setattr(playwright_adapters, "sync_playwright", lambda: fake_playwright)

    with pytest.raises(PlaywrightAdapterError, match="allowlist"):
        PlaywrightBrowserExplorer(config).explore(
            PullRequestSnapshot(
                reference=PullRequestRef("owner/storefront", 12),
                title="Title",
                description="",
                base_sha="base",
                head_sha="head",
                files=(),
                diff="",
            )
        )


def test_browser_explorer_blocks_redirect_outside_configured_allowlist(monkeypatch):
    settings = JsonConfigLoader().load(CONFIG)
    fake_playwright = FakePlaywright()

    def redirect_to_untrusted_host(_page: FakePage, _url: str, **_kwargs: object) -> None:
        fake_playwright.chromium.browser.page.url = "https://attacker.example/collect"

    monkeypatch.setattr(playwright_adapters, "sync_playwright", lambda: fake_playwright)
    monkeypatch.setattr(FakePage, "goto", redirect_to_untrusted_host)

    with pytest.raises(PlaywrightAdapterError, match="left the configured HTTPS host allowlist"):
        PlaywrightBrowserExplorer(settings.browser).explore(
            PullRequestSnapshot(
                reference=PullRequestRef("owner/storefront", 12),
                title="Title",
                description="",
                base_sha="base",
                head_sha="head",
                files=(),
                diff="",
            )
        )


def test_behavior_verifier_runs_only_configured_actions_and_assertions(monkeypatch):
    browser_config = BrowserConfig(
        start_urls=("https://store.example/cart",),
        allowed_hosts=("store.example",),
    )
    behavior_config = BehaviorConfig(
        provider="playwright",
        scenarios=(
            BehaviorScenarioConfig(
                scenario_id="apply-voucher",
                actions=(
                    BehaviorActionConfig(action="open", url="https://store.example/cart"),
                    BehaviorActionConfig(
                        action="fill", selector="input[name=promo]", value="SAVE10"
                    ),
                    BehaviorActionConfig(action="click", selector="button[type=submit]"),
                ),
                assertions=(
                    BehaviorAssertionConfig(kind="visible", selector=".discount-label"),
                    BehaviorAssertionConfig(kind="url_contains", expected="/cart"),
                ),
            ),
        ),
    )
    fake_playwright = FakePlaywright()
    monkeypatch.setattr(playwright_adapters, "sync_playwright", lambda: fake_playwright)
    verifier = PlaywrightBehaviorVerifier(browser_config, behavior_config)

    pull_request = PullRequestSnapshot(
        reference=PullRequestRef("owner/storefront", 12),
        title="Cart update",
        description="",
        base_sha="base",
        head_sha="head",
        files=(ChangedFile("src/cart.ts", "modified", 1, 0),),
        diff="",
    )
    graph_evidence = Evidence(
        evidence_id="neo4j:cart",
        source="neo4j://saleor/src/cart.ts",
        content="Confirmed route mapping",
        content_sha256="hash",
        confirmed_code_ui_mappings=(
            ConfirmedCodeUiMapping(
                changed_path="src/cart.ts",
                code_path="src/app/cart/page.tsx",
                url="https://baseline.example/cart",
            ),
        ),
    )
    results = verifier.verify(pull_request, (), (graph_evidence,))

    assert len(results.results) == 1
    assert results.results[0].scenario_id == "apply-voucher"
    assert results.results[0].status == "PASS"
    assert "2 configured assertions passed" in results.results[0].summary
    assert results.coverage_gaps == ()


def test_behavior_verifier_skips_scenario_when_changed_file_has_no_confirmed_route(
    monkeypatch,
):
    browser_config = BrowserConfig(
        start_urls=("https://store.example/cart",),
        allowed_hosts=("store.example",),
    )
    scenario = BehaviorScenarioConfig(
        scenario_id="cart-page",
        actions=(BehaviorActionConfig(action="open", url="https://store.example/cart"),),
        assertions=(BehaviorAssertionConfig(kind="visible", selector="h1"),),
    )
    fake_playwright = FakePlaywright()
    monkeypatch.setattr(playwright_adapters, "sync_playwright", lambda: fake_playwright)
    verifier = PlaywrightBehaviorVerifier(
        browser_config, BehaviorConfig(provider="playwright", scenarios=(scenario,))
    )
    pull_request = PullRequestSnapshot(
        PullRequestRef("owner/storefront", 12),
        "Change checkout",
        "",
        "base",
        "head",
        (ChangedFile("src/checkout.ts", "modified", 1, 0),),
        "diff",
    )
    unrelated_graph_evidence = Evidence(
        evidence_id="neo4j:cart",
        source="neo4j://saleor/src/cart.ts",
        content="Confirmed route mapping",
        content_sha256="hash",
        confirmed_code_ui_mappings=(
            ConfirmedCodeUiMapping(
                changed_path="src/cart.ts",
                code_path="src/app/cart/page.tsx",
                url="https://baseline.example/cart",
            ),
        ),
    )

    results = verifier.verify(pull_request, (), (unrelated_graph_evidence,))

    assert results.results == ()
    assert results.coverage_gaps == ()
    assert fake_playwright.chromium.browser.page.url == ""


def test_behavior_verifier_skips_scenario_when_confirmed_route_does_not_match(
    monkeypatch,
):
    browser_config = BrowserConfig(
        start_urls=("https://store.example/cart",),
        allowed_hosts=("store.example",),
    )
    scenario = BehaviorScenarioConfig(
        scenario_id="cart-page",
        actions=(BehaviorActionConfig(action="open", url="https://store.example/cart"),),
        assertions=(BehaviorAssertionConfig(kind="visible", selector="h1"),),
    )
    fake_playwright = FakePlaywright()
    monkeypatch.setattr(playwright_adapters, "sync_playwright", lambda: fake_playwright)
    verifier = PlaywrightBehaviorVerifier(
        browser_config, BehaviorConfig(provider="playwright", scenarios=(scenario,))
    )
    pull_request = PullRequestSnapshot(
        PullRequestRef("owner/storefront", 12),
        "Change cart",
        "",
        "base",
        "head",
        (ChangedFile("src/cart.ts", "modified", 1, 0),),
        "diff",
    )
    graph_evidence = Evidence(
        evidence_id="neo4j:products",
        source="neo4j://saleor/src/cart.ts",
        content="Confirmed route mapping",
        content_sha256="hash",
        confirmed_code_ui_mappings=(
            ConfirmedCodeUiMapping(
                changed_path="src/cart.ts",
                code_path="src/app/products/page.tsx",
                url="https://baseline.example/products",
            ),
        ),
    )

    results = verifier.verify(pull_request, (), (graph_evidence,))

    assert results.results == ()
    assert results.coverage_gaps == (
        "No configured behavior scenario covers the confirmed UI route(s): /products",
    )
    assert fake_playwright.chromium.browser.page.url == ""


def test_playwright_factory_requires_urls_and_scenarios_only_when_enabled():
    settings = JsonConfigLoader().load(CONFIG)

    with pytest.raises(ValueError, match="at least one explicit browser start URL"):
        PlaywrightAdapterFactory.create(
            settings.browser.model_copy(update={"start_urls": ()}),
            settings.behavior,
            browser_enabled=True,
            behavior_enabled=False,
        )
    with pytest.raises(ValueError, match="has no scenarios"):
        PlaywrightAdapterFactory.create(
            settings.browser,
            BehaviorConfig(provider="playwright"),
            browser_enabled=False,
            behavior_enabled=True,
        )


def test_behavior_verifier_reports_assertion_mismatch_as_failure(monkeypatch):
    browser_config = BrowserConfig(
        start_urls=("https://store.example/cart",),
        allowed_hosts=("store.example",),
    )
    behavior_config = BehaviorConfig(
        provider="playwright",
        scenarios=(
            BehaviorScenarioConfig(
                scenario_id="expect-wrong-text",
                actions=(BehaviorActionConfig(action="open", url="https://store.example/cart"),),
                assertions=(
                    BehaviorAssertionConfig(
                        kind="text_contains", selector="body", expected="not present"
                    ),
                ),
            ),
        ),
    )
    monkeypatch.setattr(playwright_adapters, "sync_playwright", FakePlaywright)
    pull_request = PullRequestSnapshot(
        reference=PullRequestRef("owner/storefront", 12),
        title="Cart update",
        description="",
        base_sha="base",
        head_sha="head",
        files=(ChangedFile("src/cart.ts", "modified", 1, 0),),
        diff="",
    )

    result = (
        PlaywrightBehaviorVerifier(browser_config, behavior_config)
        .verify(
            pull_request,
            (),
            (
                Evidence(
                    evidence_id="neo4j:cart",
                    source="neo4j://saleor/src/cart.ts",
                    content="Confirmed route mapping",
                    content_sha256="hash",
                    confirmed_code_ui_mappings=(
                        ConfirmedCodeUiMapping(
                            changed_path="src/cart.ts",
                            code_path="src/app/cart/page.tsx",
                            url="https://baseline.example/cart",
                        ),
                    ),
                ),
            ),
        )
        .results[0]
    )

    assert result.status == "FAIL"
    assert result.summary == "Element text did not contain the expected value"
