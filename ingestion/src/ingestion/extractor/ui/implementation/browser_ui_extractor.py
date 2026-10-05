"""Playwright UI explorer using explicit stable impact tags."""

import hashlib
import re
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from playwright.sync_api import Page

from ingestion.beans.decorators import component
from ingestion.config_loader.models import UiInputConfig
from ingestion.domain.models import UiObservation
from ingestion.extractor.ui.interface import UiExtractor


@component(contract=UiExtractor, name="browser")
class BrowserUiExtractor:
    def extract(self, config: UiInputConfig) -> tuple[UiObservation, ...]:
        from playwright.sync_api import sync_playwright

        observations: list[UiObservation] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                base = urlsplit(config.base_url)
                allowed_hosts = {host.lower() for host in config.allowed_hosts} or {
                    (base.hostname or "").lower()
                }
                for index, path in enumerate(config.seed_paths[: config.max_pages]):
                    page = browser.new_page()
                    response = page.goto(
                        config.base_url.rstrip("/") + path,
                        wait_until="domcontentloaded",
                        timeout=30_000,
                    )
                    observations.append(
                        self._capture(page, f"page:{index}", response.status if response else 0)
                    )
                    page.close()

                remaining_pages = max(0, config.max_pages - len(observations))
                for journey in config.journeys[:remaining_pages]:
                    page = browser.new_page()
                    response_status = 0
                    try:
                        for action in journey.actions[: config.max_actions_per_page]:
                            if action.action == "open":
                                if action.path is None:
                                    raise ValueError("Open journey action has no path")
                                response = page.goto(
                                    config.base_url.rstrip("/") + action.path,
                                    wait_until="domcontentloaded",
                                    timeout=30_000,
                                )
                                response_status = response.status if response else 0
                            elif action.action == "click":
                                if action.selector is None:
                                    raise ValueError("Click journey action has no selector")
                                page.locator(action.selector).click(timeout=30_000)
                            else:
                                if action.selector is None:
                                    raise ValueError("Wait journey action has no selector")
                                page.locator(action.selector).wait_for(
                                    state="visible", timeout=30_000
                                )
                            if (urlsplit(page.url).hostname or "").lower() not in allowed_hosts:
                                raise ValueError("UI journey navigated outside configured allowed hosts")
                        observations.append(
                            self._capture(
                                page, f"journey:{journey.journey_id}", response_status
                            )
                        )
                    finally:
                        page.close()
            finally:
                browser.close()
        return tuple(observations)

    @staticmethod
    def _capture(page: Page, observation_id: str, http_status: int) -> UiObservation:
        page_title = page.title().strip()
        page_text = re.sub(r"\s+", " ", page.locator("body").inner_text()).strip()
        control_descriptions = BrowserUiExtractor._visible_controls(page)
        visible_text = " ".join((page_text, *control_descriptions))[:20_000]
        parts = urlsplit(page.url)
        query_parameter_names = tuple(sorted({name for name, _ in parse_qsl(parts.query)}))
        stable_url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        content = f"Page title: {page_title}\nPage URL: {stable_url}\n{visible_text}"
        content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        captured = 200 <= http_status < 400 and bool(page_title) and bool(visible_text)
        content_ready = captured and visible_text.casefold() not in {"loading", "loading..."}
        tagged = page.locator("[data-impact-id]:visible")
        code_ids = tuple(
            sorted(
                {
                    value
                    for item in range(tagged.count())
                    if (value := tagged.nth(item).get_attribute("data-impact-id"))
                }
            )
        )
        return UiObservation(
            id=observation_id,
            url=stable_url,
            query_parameter_names=query_parameter_names,
            confirmed_code_ids=code_ids if content_ready else (),
            evidence_ids=(f"browser:{content_sha256}",) if captured else (),
            page_title=page_title,
            visible_text=visible_text,
            content_sha256=content_sha256,
            http_status=http_status,
            captured=captured,
            content_ready=content_ready,
        )

    @staticmethod
    def _visible_controls(page: Page) -> tuple[str, ...]:
        controls = page.locator("input:visible, textarea:visible, select:visible, button:visible")
        descriptions: list[str] = []
        for index in range(min(controls.count(), 200)):
            control = controls.nth(index)
            tag = control.evaluate("element => element.tagName.toLowerCase()")
            label = control.get_attribute("aria-label") or control.get_attribute("placeholder")
            if tag == "button":
                label = label or re.sub(r"\s+", " ", control.inner_text()).strip()
            if label:
                descriptions.append(f"Visible {tag}: {label[:160]}")
        return tuple(descriptions)
