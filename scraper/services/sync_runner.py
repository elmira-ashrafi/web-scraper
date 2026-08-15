"""Run blocking scraper work outside Django's async request context."""

from __future__ import annotations

import atexit
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar

T = TypeVar("T")

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="product-scraper")


def _close_browser_session() -> None:
    from .browser import OperaBrowserSession

    if OperaBrowserSession._instance is not None:
        OperaBrowserSession._instance.close()


def run_in_scraper_thread(func: Callable[..., T], /, *args, **kwargs) -> T:
    """Execute sync Playwright/curl work in a dedicated worker thread."""
    future = _executor.submit(func, *args, **kwargs)
    return future.result()


@atexit.register
def _shutdown_scraper_workers() -> None:
    try:
        _executor.submit(_close_browser_session).result(timeout=5)
    except Exception:
        pass
    _executor.shutdown(wait=False, cancel_futures=True)
