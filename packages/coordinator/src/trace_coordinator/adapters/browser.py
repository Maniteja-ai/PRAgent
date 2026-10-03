"""Single-operation Playwright tools. All browser work stays on one owning thread."""

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlsplit, urlunsplit

from pydantic import Field

from trace_coordinator.artifacts import save_artifact
from trace_coordinator.errors import FailureCode, ToolFailure
from trace_coordinator.ledger import canonical, digest
from trace_coordinator.models import Evidence, Record, ToolResult

SELECTOR = 'a,button,input:not([type="hidden"]),select,textarea,[role="button"]'
DESCRIBE = """el => ({tag:el.tagName.toLowerCase(), type:el.getAttribute('type')||'',
    name:el.getAttribute('aria-label') || (el.labels && el.labels[0] && el.labels[0].innerText) ||
      el.getAttribute('placeholder') || el.innerText || el.getAttribute('title') || '',
    href:el.getAttribute('href')||'', disabled:!!el.disabled,
    checked:!!el.checked, filled:!!el.value, test_id:el.getAttribute('data-testid')||''})"""


def safe_url(url):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def origin(url):
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


class NavigateInput(Record):
    environment: Literal["baseline", "patched"]
    path: str = Field(
        default="",
        max_length=2000,
        description="An observed route or configured entry path. Empty uses the configured entry route.",
    )


class ObserveInput(Record):
    environment: Literal["baseline", "patched"]


class ActionInput(ObserveInput):
    snapshot_id: str
    element_id: str
    action: Literal["click", "fill", "select"]
    value: str = Field(default="", max_length=1000)


class BrowserSession:
    def __init__(self, application, artifact_root: Path):
        self.app, self.root = application, artifact_root
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="trace-browser")
        self.playwright = self.browser = None
        self.run_id = None
        self.contexts, self.pages, self.snapshots, self.elements = {}, {}, {}, {}
        self.transitions = []
        self.known_routes = {}

    def execute(self, name, arguments, context):
        # LangGraph nodes can run on different threads; sync Playwright cannot.
        # Every action remains a separate guarded tool invocation outside this adapter.
        return self.executor.submit(self._execute, name, arguments, context).result()

    def close(self):
        try:
            self.executor.submit(self._close).result()
        finally:
            self.executor.shutdown(wait=True)

    def _close(self):
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
        self.playwright = self.browser = None
        self.contexts.clear()
        self.pages.clear()
        self.snapshots.clear()
        self.elements.clear()
        self.known_routes.clear()

    def _page(self, environment, run_id):
        if self.run_id != run_id:
            self._close()
            self.transitions = []
            self.run_id = run_id
        if not self.playwright:
            from playwright.sync_api import sync_playwright

            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(headless=self.app.browser.headless)
        if environment not in self.pages:
            deployment = getattr(self.app, environment)
            browser_context = self.browser.new_context(
                viewport={"width": 1280, "height": 900}, accept_downloads=False, service_workers="block"
            )
            page = browser_context.new_page()
            page.set_default_timeout(self.app.browser.timeout_seconds * 1000)
            page.set_default_navigation_timeout(self.app.browser.timeout_seconds * 1000)

            def guard(route):
                request = route.request
                if request.is_navigation_request() and origin(request.url) != deployment.url:
                    route.abort()
                else:
                    route.continue_()

            browser_context.route("**/*", guard)
            page.on("dialog", lambda dialog: dialog.dismiss())
            browser_context.on("page", lambda new_page: new_page.close() if new_page != page else None)
            self.contexts[environment], self.pages[environment] = browser_context, page
        return self.pages[environment]

    def _execute(self, name, arguments, context):
        if context.project_id != self.app.project_id:
            raise ToolFailure("Browser application is outside the project")
        environment = arguments.environment
        deployment = getattr(self.app, environment)
        previous = self.snapshots.get(environment) if self.run_id == context.run_id else None
        if name != "browser.navigate" and (self.run_id != context.run_id or environment not in self.pages):
            raise ToolFailure(
                "Browser session unavailable after restart; navigate before observing or acting"
            )
        page = self._page(environment, context.run_id)
        action = {"tool": name}
        if name == "browser.navigate":
            path = arguments.path or deployment.entry_path
            if not path.startswith("/") or path.startswith("//") or "\\" in path:
                raise ToolFailure("Navigation requires a route within the configured origin")
            url = urljoin(deployment.url + "/", path)
            if origin(url) != deployment.url:
                raise ToolFailure("Navigation origin is not allowed")
            if url != urljoin(
                deployment.url + "/", deployment.entry_path
            ) and url not in self.known_routes.get(environment, set()):
                raise ToolFailure("Route was not observed; navigate to the entry or click an observed link")
            self._invalidate(environment)
            response = page.goto(url, wait_until="domcontentloaded")
            if response and response.status >= 400:
                raise ToolFailure("Application route returned an HTTP error")
            action["path"] = safe_url(url)
        elif name == "browser.act":
            if arguments.snapshot_id != previous or arguments.element_id not in self.elements.get(
                environment, {}
            ):
                raise ToolFailure(
                    "Element does not belong to the latest observed screen",
                    code=FailureCode.BROWSER_STALE_SNAPSHOT,
                )
            handle, descriptor = self.elements[environment][arguments.element_id]
            current = handle.evaluate(DESCRIBE)
            current["name"] = current["name"].strip()[:250]
            if current != descriptor or not handle.is_visible() or current["disabled"]:
                raise ToolFailure("Element changed since observation; observe again")
            if arguments.action == "click":
                navigation_target = None
                if current["tag"] == "a":
                    if not current["href"] or origin(urljoin(page.url, current["href"])) != deployment.url:
                        raise ToolFailure("Link leaves the allowed origin")
                    navigation_target = urljoin(page.url, current["href"])
                elif (
                    current["name"] not in self.app.browser.allowed_button_names
                    and current["test_id"] not in self.app.browser.allowed_button_test_ids
                ):
                    raise ToolFailure(
                        "Button is not authorized by the application action policy",
                        code=FailureCode.BROWSER_ACTION_DENIED,
                    )
                self._invalidate(environment)
                handle.click()
                if navigation_target:
                    # Client-side routers may change the URL after click returns.
                    # Wait for that route before applying its readiness predicate.
                    page.wait_for_url(navigation_target, wait_until="domcontentloaded")
            else:
                if current["name"] not in self.app.browser.allowed_field_names or current["type"] in {
                    "password",
                    "file",
                }:
                    raise ToolFailure(
                        "Field is not authorized by the application action policy",
                        code=FailureCode.BROWSER_ACTION_DENIED,
                    )
                self._invalidate(environment)
                if arguments.action == "fill":
                    handle.fill(arguments.value)
                else:
                    handle.select_option(label=arguments.value)
            action.update(
                {
                    "action": arguments.action,
                    "element_id": arguments.element_id,
                    "element_name": current["name"],
                }
            )
        if origin(page.url) != deployment.url:
            raise ToolFailure("Browser left its configured environment")
        page.wait_for_timeout(self.app.browser.settle_ms)
        matches = [
            prefix
            for prefix in self.app.browser.readiness_checks
            if urlsplit(page.url).path.startswith(prefix)
        ]
        if matches:
            selected = self.app.browser.readiness_checks[max(matches, key=len)]
            page.locator(selected).first.wait_for(state="visible")
        return self._capture(page, environment, context, previous, action)

    def _invalidate(self, environment):
        # A mutation may succeed before capture fails. Old handles must not
        # authorize another action until a fresh observation succeeds.
        self.snapshots.pop(environment, None)
        self.elements.pop(environment, None)

    def _capture(self, page, environment, context, previous, action):
        elements, live_handles = [], {}
        for handle in page.query_selector_all(SELECTOR):
            if len(elements) >= self.app.browser.max_elements:
                break
            if not handle.is_visible():
                continue
            item = handle.evaluate(DESCRIBE)
            item["name"] = item["name"].strip()[:250]
            if item["type"] in {"password", "file"}:
                continue
            element_id = f"element-{len(elements) + 1}"
            live_handles[element_id] = (handle, item)
            if item["href"]:
                destination = urljoin(page.url, item["href"])
                if origin(destination) == getattr(self.app, environment).url:
                    self.known_routes.setdefault(environment, set()).add(destination)
            public = {**item, "href": safe_url(urljoin(page.url, item["href"])) if item["href"] else ""}
            elements.append({"id": element_id, **public})
        text = page.locator("body").inner_text()
        text_truncated = len(text) > self.app.browser.max_text_chars
        text = text[: self.app.browser.max_text_chars]
        # Capture DOM structure without scripts, hidden inputs or form values.
        dom = page.evaluate("""() => {const root=document.documentElement.cloneNode(true);
            root.querySelectorAll('script,style,input[type=hidden],noscript').forEach(e=>e.remove());
            root.querySelectorAll('*').forEach(e=>{for(const a of [...e.attributes]) {
                if(!['role','aria-label','aria-expanded','aria-checked','type','name','placeholder','id','class'].includes(a.name)) e.removeAttribute(a.name);
            }}); return root.outerHTML;}""")
        if len(dom) > self.app.browser.max_dom_chars:
            raise ToolFailure("DOM exceeds capture budget; no incomplete DOM artifact accepted")
        image = page.screenshot(full_page=False, mask=[page.locator('input[type="password"]')])
        observation = {
            "environment": environment,
            "url": safe_url(page.url),
            "captured_at": time.time(),
            "text": text,
            "text_truncated": text_truncated,
            "elements": elements,
            "element_limit_reached": len(elements) >= self.app.browser.max_elements,
            "claimed_revision": getattr(self.app, environment).revision,
            "runtime_build_attested": False,
        }
        observation["state_fingerprint"] = digest(
            {
                "url": observation["url"],
                "text": text,
                "elements": elements,
            }
        )
        snapshot_id = "ui:" + digest(observation)[:24]
        dom_ref = save_artifact(self.root, context.run_id, dom.encode(), ".dom.html")
        screenshot_ref = save_artifact(self.root, context.run_id, image, ".png")
        data_ref = save_artifact(
            self.root, context.run_id, canonical(observation).encode(), ".observation.json"
        )
        transition = {"environment": environment, "from": previous, "to": snapshot_id, **action}
        transition_ref = save_artifact(
            self.root, context.run_id, canonical(transition).encode(), ".transition.json"
        )
        self.snapshots[environment], self.elements[environment] = snapshot_id, live_handles
        self.transitions.append(transition)
        return ToolResult(
            evidence=(
                Evidence(
                    id=snapshot_id,
                    project_id=context.project_id,
                    kind="browser",
                    summary=canonical(observation),
                    source=safe_url(page.url),
                    metadata={
                        "dom": dom_ref,
                        "screenshot": screenshot_ref,
                        "observation": data_ref,
                        "transition": transition_ref,
                        "environment": environment,
                        "transition_record": transition,
                    },
                ),
            ),
            gaps=(
                "Browser captures establish observed UI only; no automated before/after assertion was made.",
            ),
        )


class BrowserTool:
    allowed_agents = frozenset({"coordinator"})

    def __init__(self, name, session):
        self.name, self.session = name, session
        self.input_model = {
            "browser.navigate": NavigateInput,
            "browser.observe": ObserveInput,
            "browser.act": ActionInput,
        }[name]
        self.description = {
            "browser.navigate": "Navigate to a configured environment and capture DOM, screenshot and observed element IDs.",
            "browser.observe": "Capture the current environment again; no navigation or interaction.",
            "browser.act": "Perform ONE action on an element from the latest snapshot, then capture the transition. No unobserved selectors.",
        }[name]
        if name == "browser.act":
            self.description += (
                f" Allowed button names: {list(session.app.browser.allowed_button_names)}."
                f" Allowed field names: {list(session.app.browser.allowed_field_names)}."
                f" Alternatively allowed observed button test_id values: {list(session.app.browser.allowed_button_test_ids)}."
            )
        self.version = "playwright-v3:" + digest(session.app.model_dump(mode="json"))

    def execute(self, arguments, context):
        return self.session.execute(self.name, arguments, context)
