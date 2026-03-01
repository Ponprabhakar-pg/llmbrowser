"""
Multi-viewport runner for responsive web app QA.

Usage
-----
    from llmbrowser import LLMBrowser, BrowserToolkit, run_at_viewports, VIEWPORTS

    async def check_nav(browser: LLMBrowser) -> str:
        toolkit = BrowserToolkit(browser)
        await toolkit.execute("navigate", url="https://myapp.com")
        state = await toolkit.execute("get_page_state")
        return state

    results = await run_at_viewports(check_nav, viewports=["mobile", "desktop"])

    for r in results:
        print(r.viewport, r.width, "x", r.height, "→", "OK" if r.success else r.error)

Custom viewports
----------------
    results = await run_at_viewports(
        check_nav,
        viewports={"iphone-se": {"width": 375, "height": 667}},
    )
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Union

from .core import LLMBrowser

# ── Viewport presets ──────────────────────────────────────────────────────────

VIEWPORTS: dict[str, dict[str, int]] = {
    "mobile":  {"width": 375,  "height": 812},   # iPhone 14 Pro
    "tablet":  {"width": 768,  "height": 1024},  # iPad
    "desktop": {"width": 1280, "height": 800},   # standard laptop
}

# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class ViewportResult:
    """Result from running a task at a single viewport."""
    viewport:  str          # preset name or key from custom dict
    width:     int
    height:    int
    result:    Any          # return value of your task function (None on error)
    success:   bool         # False if the task raised an exception
    error:     str          # exception message, empty string on success
    elapsed_s: float

    def __repr__(self) -> str:
        status = "OK" if self.success else f"ERROR({self.error[:40]})"
        return f"<ViewportResult {self.viewport} {self.width}x{self.height} {status}>"


# ── Runner ────────────────────────────────────────────────────────────────────

TaskFn = Callable[[LLMBrowser], Coroutine[Any, Any, Any]]


async def run_at_viewports(
    task:        TaskFn,
    viewports:   Union[list[str], dict[str, dict[str, int]]] = ("mobile", "tablet", "desktop"),
    concurrency: int = 1,
    headless:    bool = True,
    **browser_kwargs: Any,
) -> list[ViewportResult]:
    """
    Run *task* once per viewport and return a list of ViewportResult.

    Parameters
    ----------
    task:
        An async callable that accepts an ``LLMBrowser`` and returns any value.
        A fresh browser is created for each viewport.

    viewports:
        • A list of preset names: ``["mobile", "desktop"]``
          (valid presets: ``"mobile"``, ``"tablet"``, ``"desktop"``)
        • Or a dict mapping name → ``{"width": int, "height": int}`` for custom
          sizes: ``{"iphone-se": {"width": 375, "height": 667}}``

    concurrency:
        How many viewports to test in parallel (default: 1 = sequential).
        Keep low if testing against rate-limited services.

    headless:
        Whether to run the browser headlessly (default: True).

    **browser_kwargs:
        Extra keyword arguments forwarded to ``LLMBrowser`` (e.g. ``stealth=True``,
        ``proxy="http://..."``, ``browser="firefox"``).

    Returns
    -------
    list[ViewportResult]
        One entry per viewport, in the same order as *viewports*.
    """
    # Normalise viewports into a list of (name, size_dict) pairs
    if isinstance(viewports, dict):
        vp_pairs = list(viewports.items())
    else:
        vp_pairs = []
        for name in viewports:
            if name not in VIEWPORTS:
                raise ValueError(
                    f"Unknown viewport preset '{name}'. "
                    f"Valid presets: {list(VIEWPORTS)}. "
                    "Pass a dict to use custom dimensions."
                )
            vp_pairs.append((name, VIEWPORTS[name]))

    sem = asyncio.Semaphore(concurrency)

    async def _run_one(name: str, size: dict[str, int]) -> ViewportResult:
        async with sem:
            t0 = time.perf_counter()
            result = None
            error  = ""
            success = False
            try:
                async with LLMBrowser(
                    headless=headless,
                    viewport=size,
                    **browser_kwargs,
                ) as browser:
                    result  = await task(browser)
                    success = True
            except Exception as exc:
                error = str(exc)
            elapsed = round(time.perf_counter() - t0, 2)
            return ViewportResult(
                viewport  = name,
                width     = size["width"],
                height    = size["height"],
                result    = result,
                success   = success,
                error     = error,
                elapsed_s = elapsed,
            )

    coros = [_run_one(name, size) for name, size in vp_pairs]
    return list(await asyncio.gather(*coros))
