"""Fetch product pages with a persistent Playwright Opera browser session."""

from __future__ import annotations

import concurrent.futures
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

DEFAULT_TIMEOUT_MS = 60_000
POLL_INTERVAL_MS = 500
CDP_READY_TIMEOUT_S = 20

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


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


def _read_stderr_tail(log_path: str, max_lines: int = 300, max_chars: int = 12000) -> str:
    try:
        with open(log_path, "r", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return "(no stderr captured)"

    # Collapse consecutive duplicate lines (dbus/GSettings spam repeats a lot
    # and can push the one unique fatal line out of a small tail).
    deduped: list[str] = []
    for line in lines:
        if deduped and deduped[-1] == line:
            continue
        deduped.append(line)

    tail = deduped[-max_lines:]
    content = "".join(tail).strip()
    return content[-max_chars:]


def _wait_for_cdp(
    port: int, process: subprocess.Popen, timeout_s: float, log_path: str
) -> str:
    """Poll the CDP HTTP endpoint until Opera is ready, return the ws endpoint base."""
    endpoint = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + timeout_s

    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr_tail = _read_stderr_tail(log_path)
            raise RuntimeError(
                f"Opera process exited early (code={process.returncode}) "
                "before exposing the remote debugging port.\n"
                f"--- Opera stderr tail ---\n{stderr_tail}"
            )
        try:
            with urllib.request.urlopen(f"{endpoint}/json/version", timeout=1):
                return endpoint
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.2)

    process.kill()
    stderr_tail = _read_stderr_tail(log_path)
    raise RuntimeError(
        "Opera did not expose the remote debugging port in time.\n"
        f"--- Opera stderr tail ---\n{stderr_tail}"
    )


def _spawn_opera(executable_path: str, *, headless: bool) -> tuple[subprocess.Popen, int, str, str]:
    port = _find_free_port()
    user_data_dir = tempfile.mkdtemp(prefix="opera-profile-")
    log_fd, log_path = tempfile.mkstemp(prefix="opera-stderr-", suffix=".log")

    args = [
        executable_path,
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={user_data_dir}",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-setuid-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-default-apps",
        "--disable-sync",
        "--no-first-run",
        "--no-default-browser-check",
        "--metrics-recording-only",
        "--mute-audio",
        "about:blank",
    ]
    if headless:
        args.insert(1, "--headless")

    with os.fdopen(log_fd, "wb", closefd=True) as log_file:
        process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=log_file,
        )
    return process, port, user_data_dir, log_path


def launch_opera(playwright: Playwright, *, headless: bool = True) -> tuple[Browser, subprocess.Popen, str]:
    executable_path = resolve_opera_executable()
    process, port, user_data_dir, log_path = _spawn_opera(executable_path, headless=headless)

    try:
        endpoint = _wait_for_cdp(port, process, CDP_READY_TIMEOUT_S, log_path)
        try:
            browser = playwright.chromium.connect_over_cdp(endpoint)
        except Exception as exc:
            stderr_tail = _read_stderr_tail(log_path)
            exit_code = process.poll()
            raise RuntimeError(
                f"Failed to attach to Opera over CDP ({exc}). "
                f"Process exit code at that moment: {exit_code}.\n"
                f"--- Opera stderr tail ---\n{stderr_tail}"
            ) from exc
    except Exception:
        process.kill()
        raise
    finally:
        try:
            os.remove(log_path)
        except OSError:
            pass

    return browser, process, user_data_dir


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
        self._process: subprocess.Popen | None = None
        self._user_data_dir: str | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._headless = True
        # Playwright's sync API must always run on the same plain OS thread,
        # and that thread must never have an asyncio event loop attached to it
        # (Django/asgiref worker threads sometimes do). A single-worker
        # executor gives us one dedicated, plain thread for the whole
        # lifetime of the process, isolated from whatever thread Django
        # happens to call us from.
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="opera-browser"
        )

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
        self._browser, self._process, self._user_data_dir = launch_opera(
            self._playwright, headless=headless
        )
        self._context = self._create_context()
        self._page = self._context.new_page()

    def _reset_storage(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
            self._page = None

        self._context = self._create_context()
        self._page = self._context.new_page()

    def _close_impl(self) -> None:
        with self._lock:
            if self._context is not None:
                try:
                    self._context.close()
                except Exception:
                    pass
                self._context = None
                self._page = None

            if self._browser is not None:
                try:
                    self._browser.close()
                except Exception:
                    pass
                self._browser = None

            if self._playwright is not None:
                self._playwright.stop()
                self._playwright = None

            if self._process is not None:
                self._process.kill()
                self._process.wait(timeout=5)
                self._process = None

            if self._user_data_dir is not None:
                shutil.rmtree(self._user_data_dir, ignore_errors=True)
                self._user_data_dir = None

    def close(self) -> None:
        """Tear down the browser. Safe to call from any thread."""
        future = self._executor.submit(self._close_impl)
        future.result()
        self._executor.shutdown(wait=True)

    def _fetch_page_impl(
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
        """Fetch a page. Safe to call from any thread (Django view, etc.) —
        the actual Playwright work always runs on the dedicated browser thread.
        """
        future = self._executor.submit(
            self._fetch_page_impl,
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
        return future.result()


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