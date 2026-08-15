"""Fetch product pages with a persistent Playwright Opera browser session."""

from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

DEFAULT_TIMEOUT_MS = 60_000
POLL_INTERVAL_MS = 500

OPERA_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 OPR/120.0.0.0"
)

STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
});
"""

OPERA_EXECUTABLE_CANDIDATES = (
    "/usr/bin/opera",
    "/usr/bin/opera-stable",
    "/snap/bin/opera",
    "/Applications/Opera.app/Contents/MacOS/Opera",
    r"C:\Program Files\Opera\opera.exe",
    r"C:\Program Files (x86)\Opera\opera.exe",
)


def resolve_opera_executable() -> str:
    configured = os.environ.get("OPERA_PATH", "").strip()
    if configured:
        path = Path(configured)
        if path.is_file():
            return str(path)
        raise RuntimeError(f"OPERA_PATH does not point to a file: {configured}")

    for candidate in OPERA_EXECUTABLE_CANDIDATES:
        if Path(candidate).is_file():
            return candidate

    discovered = shutil.which("opera") or shutil.which("opera-stable")
    if discovered:
        return discovered

    raise RuntimeError(
        "Opera was not found. Install Opera or set OPERA_PATH to the browser executable."
    )


def launch_opera(playwright: Playwright, *, headless: bool = True) -> Browser:
    executable_path = resolve_opera_executable()
    return playwright.chromium.launch(
        executable_path=executable_path,
        headless=headless,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ],
    )


def page_contains_markers(page: Page, markers: tuple[str, ...]) -> bool:
    if not markers:
        return True
    return bool(
        page.evaluate(
            """(markers) => {
                const html = document.documentElement ? document.documentElement.innerHTML : "";
                return markers.some((marker) => html.includes(marker));
            }""",
            list(markers),
        )
    )


def page_html_length(page: Page) -> int:
    return int(
        page.evaluate(
            """() => document.documentElement ? document.documentElement.innerHTML.length : 0"""
        )
    )


def trigger_lazy_content(page: Page) -> None:
    try:
        page.evaluate(
            """() => {
                window.scrollTo(0, 500);
                window.scrollTo(0, 0);
            }"""
        )
    except Exception:
        pass


def wait_for_page_text(
    page: Page,
    marker: str,
    timeout_ms: int,
    *,
    fail_texts: tuple[str, ...] = (),
) -> None:
    wait_for_any_page_text(page, (marker,), timeout_ms, fail_texts=fail_texts)


def wait_for_any_page_text(
    page: Page,
    markers: tuple[str, ...],
    timeout_ms: int,
    *,
    fail_texts: tuple[str, ...] = (),
    required: bool = True,
) -> bool:
    if not markers:
        return True

    deadline = time.monotonic() + (timeout_ms / 1000)
    last_length = 0
    stuck_checks = 0
    reloaded = False

    while time.monotonic() < deadline:
        if fail_texts and page_contains_markers(page, fail_texts):
            raise RuntimeError(
                f"Blocked page detected while loading: {fail_texts[0]}"
            )

        if page_contains_markers(page, markers):
            return True

        current_length = page_html_length(page)
        if current_length <= last_length and current_length < 150_000:
            stuck_checks += 1
        else:
            stuck_checks = 0
        last_length = current_length

        if stuck_checks >= 20 and not reloaded:
            try:
                page.reload(wait_until="commit", timeout=min(timeout_ms, 30_000))
                trigger_lazy_content(page)
            except Exception:
                pass
            reloaded = True
            stuck_checks = 0
            last_length = 0

        if stuck_checks and stuck_checks % 6 == 0:
            trigger_lazy_content(page)

        page.wait_for_timeout(POLL_INTERVAL_MS)

    if required:
        raise TimeoutError(f"Timed out waiting for page markers: {', '.join(markers)}")
    return False


def load_page_in_browser(
    page: Page,
    url: str,
    *,
    timeout_ms: int,
    ready_text: str | None = None,
    ready_text_any: tuple[str, ...] = (),
    ready_stages: tuple[tuple[str, ...], ...] = (),
    optional_ready_stages: tuple[tuple[str, ...], ...] = (),
    fail_texts: tuple[str, ...] = (),
    settle_ms: int = 2000,
) -> str:
    page.goto(url, wait_until="commit", timeout=timeout_ms)
    trigger_lazy_content(page)

    if ready_text:
        wait_for_page_text(page, ready_text, timeout_ms, fail_texts=fail_texts)

    if ready_text_any:
        wait_for_any_page_text(page, ready_text_any, timeout_ms, fail_texts=fail_texts)

    for stage_markers in ready_stages:
        wait_for_any_page_text(page, stage_markers, timeout_ms, fail_texts=fail_texts)

    for stage_markers in optional_ready_stages:
        wait_for_any_page_text(
            page,
            stage_markers,
            timeout_ms,
            fail_texts=fail_texts,
            required=False,
        )

    if settle_ms > 0:
        page.wait_for_timeout(settle_ms)
    return page.content()


class OperaBrowserSession:
    """Keeps one Opera browser alive and reuses it across scrapes."""

    _instance: OperaBrowserSession | None = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._headless = True

    @classmethod
    def get(cls) -> OperaBrowserSession:
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _create_context(self) -> BrowserContext:
        if not self._browser:
            raise RuntimeError("Opera browser is not running.")

        context = self._browser.new_context(
            locale="en-US",
            timezone_id="America/New_York",
            viewport={"width": 1440, "height": 900},
            user_agent=OPERA_USER_AGENT,
        )
        context.add_init_script(STEALTH_INIT_SCRIPT)
        return context

    def _ensure_running(self, *, headless: bool = True) -> None:
        self._headless = headless
        if self._browser is not None:
            return

        self._playwright = sync_playwright().start()
        self._browser = launch_opera(self._playwright, headless=headless)
        self._context = self._create_context()
        self._page = self._context.new_page()

    def _reset_storage(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
            self._page = None

        self._context = self._create_context()
        self._page = self._context.new_page()

    def close(self) -> None:
        with self._lock:
            if self._context is not None:
                self._context.close()
                self._context = None
                self._page = None

            if self._browser is not None:
                self._browser.close()
                self._browser = None

            if self._playwright is not None:
                self._playwright.stop()
                self._playwright = None

    def fetch_page(
        self,
        url: str,
        *,
        timeout: int = 60,
        ready_text: str | None = None,
        ready_text_any: tuple[str, ...] = (),
        ready_stages: tuple[tuple[str, ...], ...] = (),
        optional_ready_stages: tuple[tuple[str, ...], ...] = (),
        fail_texts: tuple[str, ...] = (),
        settle_ms: int = 2000,
        headless: bool = True,
        reset_storage: bool = False,
    ) -> str:
        timeout_ms = max(timeout, 1) * 1000

        with self._lock:
            self._ensure_running(headless=headless)
            if reset_storage:
                self._reset_storage()
            elif self._page is None:
                self._page = self._context.new_page()

            assert self._page is not None
            return load_page_in_browser(
                self._page,
                url,
                timeout_ms=timeout_ms,
                ready_text=ready_text,
                ready_text_any=ready_text_any,
                ready_stages=ready_stages,
                optional_ready_stages=optional_ready_stages,
                fail_texts=fail_texts,
                settle_ms=settle_ms,
            )


def fetch_page(
    url: str,
    *,
    timeout: int = 60,
    ready_text: str | None = None,
    ready_text_any: tuple[str, ...] = (),
    ready_stages: tuple[tuple[str, ...], ...] = (),
    optional_ready_stages: tuple[tuple[str, ...], ...] = (),
    fail_texts: tuple[str, ...] = (),
    settle_ms: int = 2000,
    headless: bool = True,
    reset_storage: bool = False,
) -> str:
    """Open a URL in the shared Opera session and return the rendered HTML."""
    return OperaBrowserSession.get().fetch_page(
        url,
        timeout=timeout,
        ready_text=ready_text,
        ready_text_any=ready_text_any,
        ready_stages=ready_stages,
        optional_ready_stages=optional_ready_stages,
        fail_texts=fail_texts,
        settle_ms=settle_ms,
        headless=headless,
        reset_storage=reset_storage,
    )
