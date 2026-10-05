"""Playwright adapters for read-only UI exploration and configured behavior checks."""

import hashlib
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

from impact_agent.config.validation.behavior import (
    BehaviorActionConfig,
    BehaviorAssertionConfig,
    BehaviorConfig,
    BehaviorScenarioConfig,
)
from impact_agent.config.validation.browser import BrowserConfig
from impact_agent.domain.models import (
    BehaviorResult,
    BehaviorVerification,
    Evidence,
    Finding,
    PullRequestSnapshot,
)
from impact_agent.tools.behavior.interface.behavior_verifier import BehaviorVerifier
from impact_agent.tools.browser.interface.browser_explorer import BrowserExplorer


class PlaywrightAdapterError(RuntimeError):
    """A browser operation could not be completed safely."""


class _ScenarioAssertionError(AssertionError):
    """A live page did not match a configured behavior assertion."""


class PlaywrightBrowserExplorer(BrowserExplorer):
    """Capture bounded, read-only page text from explicitly allowed storefront URLs."""

    def __init__(self, config: BrowserConfig) -> None:
        self._config = config
        self._allowed_hosts = {host.lower() for host in config.allowed_hosts}

    def explore(self, pull_request: PullRequestSnapshot) -> tuple[Evidence, ...]:
        del pull_request  # The configured public pages are inspected consistently per PR.
        evidence: list[Evidence] = []
        try:
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True)
                except PlaywrightError as error:
                    raise PlaywrightAdapterError("Playwright browser could not start") from error
                try:
                    page = browser.new_page()
                    page.set_default_timeout(self._timeout_ms)
                    for url in self._config.start_urls:
                        self._validate_url(url)
                        page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)
                        self._validate_current_url(page)
                        title = page.title()
                        body = page.locator("body").inner_text(timeout=self._timeout_ms)
                        content = (
                            f"Page title: {title}\nPage URL: {page.url}\nVisible page text:\n{body}"
                        )
                        content = content[: self._config.max_page_characters]
                        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                        url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
                        evidence.append(
                            Evidence(
                                evidence_id=f"browser:{url_hash}",
                                source=f"browser://{urlsplit(url).netloc}{urlsplit(url).path}",
                                content=content,
                                content_sha256=content_hash,
                            )
                        )
                finally:
                    browser.close()
        except PlaywrightError as error:
            raise PlaywrightAdapterError("Browser exploration failed") from error
        return tuple(evidence)

    @property
    def _timeout_ms(self) -> int:
        return int(self._config.timeout_seconds * 1000)

    def _validate_url(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname is None:
            raise PlaywrightAdapterError("Browser URL must use HTTPS and include a hostname")
        if parsed.hostname.lower() not in self._allowed_hosts:
            raise PlaywrightAdapterError("Browser URL host is not in the configured allowlist")

    def _validate_current_url(self, page: Page) -> None:
        parsed = urlsplit(page.url)
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.hostname.lower() not in self._allowed_hosts
        ):
            raise PlaywrightAdapterError(
                "Browser navigation left the configured HTTPS host allowlist"
            )


class PlaywrightBehaviorVerifier(BehaviorVerifier):
    """Run only the explicit actions and assertions stored in behavior.json."""

    def __init__(self, browser_config: BrowserConfig, behavior_config: BehaviorConfig) -> None:
        self._browser_config = browser_config
        self._behavior_config = behavior_config
        self._allowed_hosts = {host.lower() for host in browser_config.allowed_hosts}
        self._validate_scenario_urls()

    def verify(
        self,
        pull_request: PullRequestSnapshot,
        findings: tuple[Finding, ...],
        evidence: tuple[Evidence, ...],
    ) -> BehaviorVerification:
        del findings
        changed_paths = {changed_file.path for changed_file in pull_request.files}
        mapped_routes = {
            self._route_path(mapping.url)
            for item in evidence
            for mapping in item.confirmed_code_ui_mappings
            if mapping.changed_path in changed_paths
        }
        applicable_scenarios = tuple(
            scenario
            for scenario in self._behavior_config.scenarios
            if self._scenario_routes(scenario).intersection(mapped_routes)
        )
        covered_routes = {
            route for scenario in applicable_scenarios for route in self._scenario_routes(scenario)
        }
        uncovered_routes = sorted(mapped_routes - covered_routes)
        coverage_gaps: list[str] = []
        if uncovered_routes:
            coverage_gaps.append(
                "No configured behavior scenario covers the confirmed UI route(s): "
                + ", ".join(uncovered_routes)
            )
        for scenario in applicable_scenarios:
            if all(
                assertion.kind in {"visible", "url_contains"} for assertion in scenario.assertions
            ):
                coverage_gaps.append(
                    f"Behavior scenario '{scenario.scenario_id}' checks page/control presence or "
                    "URL only; business behavior outcomes are unverified."
                )
        return BehaviorVerification(
            results=tuple(self._run_scenario(scenario) for scenario in applicable_scenarios),
            coverage_gaps=tuple(coverage_gaps),
        )

    @classmethod
    def _scenario_routes(cls, scenario: BehaviorScenarioConfig) -> set[str]:
        if scenario.target_routes:
            return {cls._route_path(route) for route in scenario.target_routes}
        return {
            cls._route_path(action.url)
            for action in scenario.actions
            if action.action == "open" and action.url is not None
        }

    @staticmethod
    def _route_path(url: str) -> str:
        """Compare route paths across baseline and patched deployment hostnames."""
        return urlsplit(url).path.rstrip("/") or "/"

    def _run_scenario(self, scenario: BehaviorScenarioConfig) -> BehaviorResult:
        verified_checks: list[str] = []
        try:
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(headless=True)
                except PlaywrightError as error:
                    return BehaviorResult(
                        scenario_id=scenario.scenario_id,
                        status="BLOCKED",
                        summary=f"The Playwright browser could not start ({type(error).__name__}).",
                    )
                try:
                    page = browser.new_page()
                    page.set_default_timeout(self._timeout_ms)
                    for action in scenario.actions:
                        self._apply_action(page, action)
                        self._validate_current_url(page)
                    for assertion in scenario.assertions:
                        self._validate_current_url(page)
                        self._check_assertion(page, assertion)
                        verified_checks.append(self._describe_assertion(assertion))
                finally:
                    browser.close()
            return BehaviorResult(
                scenario_id=scenario.scenario_id,
                status="PASS",
                summary=f"All {len(scenario.assertions)} configured assertions passed.",
                verified_checks=tuple(verified_checks),
            )
        except _ScenarioAssertionError as error:
            return BehaviorResult(
                scenario_id=scenario.scenario_id,
                status="FAIL",
                summary=str(error),
                verified_checks=tuple(verified_checks),
            )
        except PlaywrightError as error:
            return BehaviorResult(
                scenario_id=scenario.scenario_id,
                status="FAIL",
                summary=f"A browser action or assertion failed ({type(error).__name__}).",
                verified_checks=tuple(verified_checks),
            )
        except PlaywrightAdapterError as error:
            return BehaviorResult(
                scenario_id=scenario.scenario_id,
                status="BLOCKED",
                summary=str(error),
                verified_checks=tuple(verified_checks),
            )

    def _apply_action(self, page: Page, action: BehaviorActionConfig) -> None:
        if action.action == "open":
            if action.url is None:
                raise PlaywrightAdapterError("Configured open action is missing its URL")
            self._validate_url(action.url)
            page.goto(action.url, wait_until="domcontentloaded", timeout=self._timeout_ms)
        elif action.action == "click":
            if action.selector is None:
                raise PlaywrightAdapterError("Configured click action is missing its selector")
            page.locator(action.selector).click(timeout=self._timeout_ms)
        elif action.action == "fill":
            if action.selector is None or action.value is None:
                raise PlaywrightAdapterError("Configured fill action is incomplete")
            page.locator(action.selector).fill(action.value, timeout=self._timeout_ms)
        elif action.action == "press":
            if action.selector is None or action.value is None:
                raise PlaywrightAdapterError("Configured press action is incomplete")
            page.locator(action.selector).press(action.value, timeout=self._timeout_ms)

    def _check_assertion(self, page: Page, assertion: BehaviorAssertionConfig) -> None:
        if assertion.kind == "url_contains":
            expected = assertion.expected
            if expected is None or expected not in page.url:
                raise _ScenarioAssertionError("Current page URL did not match the expected value")
            return
        if assertion.selector is None:
            raise PlaywrightAdapterError("Configured assertion is missing its selector")
        element = page.locator(assertion.selector)
        if assertion.kind == "visible":
            element.wait_for(state="visible", timeout=self._timeout_ms)
        elif assertion.kind == "text_contains":
            expected = assertion.expected
            if expected is None:
                raise PlaywrightAdapterError("Configured text assertion is missing its value")
            actual = element.inner_text(timeout=self._timeout_ms)
            if expected not in actual:
                raise _ScenarioAssertionError("Element text did not contain the expected value")
        elif assertion.kind == "value_equals":
            expected = assertion.expected
            if expected is None:
                raise PlaywrightAdapterError("Configured value assertion is missing its value")
            if element.input_value(timeout=self._timeout_ms) != expected:
                raise _ScenarioAssertionError("Input value did not equal the expected value")

    @staticmethod
    def _describe_assertion(assertion: BehaviorAssertionConfig) -> str:
        if assertion.kind == "url_contains":
            return f"URL contains {assertion.expected!r}"
        if assertion.kind == "visible":
            return f"{assertion.selector} is visible"
        if assertion.kind == "text_contains":
            return f"{assertion.selector} text contains {assertion.expected!r}"
        return f"{assertion.selector} value equals {assertion.expected!r}"

    @property
    def _timeout_ms(self) -> int:
        return int(self._browser_config.timeout_seconds * 1000)

    def _validate_scenario_urls(self) -> None:
        for scenario in self._behavior_config.scenarios:
            for action in scenario.actions:
                if action.action == "open" and action.url is not None:
                    self._validate_url(action.url)

    def _validate_url(self, url: str) -> None:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname is None:
            raise ValueError("Behavior scenario URL must use HTTPS and include a hostname")
        if parsed.hostname.lower() not in self._allowed_hosts:
            raise ValueError("Behavior scenario URL host is not in browser.allowed_hosts")

    def _validate_current_url(self, page: Page) -> None:
        parsed = urlsplit(page.url)
        if (
            parsed.scheme != "https"
            or parsed.hostname is None
            or parsed.hostname.lower() not in self._allowed_hosts
        ):
            raise PlaywrightAdapterError(
                "Browser navigation left the configured HTTPS host allowlist"
            )


class PlaywrightAdapterFactory:
    """Build selected browser ports from the split browser and behavior settings."""

    @staticmethod
    def create(
        browser_config: BrowserConfig,
        behavior_config: BehaviorConfig,
        *,
        browser_enabled: bool,
        behavior_enabled: bool,
    ) -> tuple[BrowserExplorer | None, BehaviorVerifier | None]:
        browser: BrowserExplorer | None = None
        verifier: BehaviorVerifier | None = None
        if browser_enabled:
            if browser_config.provider != "playwright":
                raise ValueError("Browser stage is enabled but browser.json disables Playwright")
            if not browser_config.start_urls:
                raise ValueError("Browser stage needs at least one explicit browser start URL")
            browser = PlaywrightBrowserExplorer(browser_config)
        if behavior_enabled:
            if browser_config.provider != "playwright":
                raise ValueError("Behavior checks require the browser.json Playwright provider")
            if behavior_config.provider != "playwright":
                raise ValueError(
                    "Behavior checks are enabled but behavior.json disables Playwright"
                )
            if not behavior_config.scenarios:
                raise ValueError("Behavior checks are enabled but behavior.json has no scenarios")
            verifier = PlaywrightBehaviorVerifier(browser_config, behavior_config)
        return browser, verifier
