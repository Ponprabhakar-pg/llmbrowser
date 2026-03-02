import base64
import logging
import shutil
import sys
from pathlib import Path
from typing import Optional

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

logger = logging.getLogger(__name__)

# Wait for DOM mutations to settle: resolves after SETTLE_DELAY ms of inactivity
# or when the hard timeout fires, whichever comes first.
_SETTLE_JS = """
(timeoutMs) => new Promise(resolve => {
    const SETTLE_DELAY = 300;
    let settled = false;
    let debounceTimer = null;
    let hardTimer = null;

    const finish = () => {
        if (settled) return;
        settled = true;
        observer.disconnect();
        clearTimeout(debounceTimer);
        clearTimeout(hardTimer);
        resolve();
    };

    const reschedule = () => {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(finish, SETTLE_DELAY);
    };

    const observer = new MutationObserver(reschedule);
    observer.observe(document.documentElement, {
        childList: true,
        subtree: true,
        attributes: true,
        characterData: true,
    });

    reschedule();                             // settle immediately if DOM is already quiet
    hardTimer = setTimeout(finish, timeoutMs); // safety valve
})
"""


def find_chrome_binary() -> Optional[str]:
    """
    Locate the system-installed Chrome, Chromium, or Edge binary.

    Checks OS-specific installation paths first, then falls back to PATH lookup.
    Returns the path string, or None if nothing is found.
    """
    if sys.platform == "darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
        path_names = ["google-chrome", "chromium", "chromium-browser"]
    elif sys.platform == "win32":
        import os
        pf   = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        pf86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        lad  = os.environ.get("LOCALAPPDATA", "")
        candidates = [
            rf"{pf}\Google\Chrome\Application\chrome.exe",
            rf"{pf86}\Google\Chrome\Application\chrome.exe",
            rf"{lad}\Google\Chrome\Application\chrome.exe",
            rf"{pf}\Microsoft\Edge\Application\msedge.exe",
            rf"{pf86}\Microsoft\Edge\Application\msedge.exe",
        ]
        path_names = ["chrome", "msedge"]
    else:  # Linux / other POSIX
        candidates = []
        path_names = [
            "google-chrome",
            "google-chrome-stable",
            "chromium",
            "chromium-browser",
            "microsoft-edge",
            "microsoft-edge-stable",
        ]

    for path in candidates:
        if Path(path).exists():
            return path

    for name in path_names:
        found = shutil.which(name)
        if found:
            return found

    return None


def screenshot_to_base64(screenshot_bytes: bytes) -> str:
    return base64.b64encode(screenshot_bytes).decode("utf-8")


async def safe_wait(page: Page, timeout: int = 5_000) -> None:
    """
    Wait for the page to settle using a MutationObserver debounce.

    Resolves after 300 ms of DOM inactivity, or when the hard timeout fires.
    More reliable than network-idle polling for SPAs, streaming pages, and
    sites that hold open long-poll connections.

    Falls back to domcontentloaded load-state if JavaScript evaluation fails.
    """
    try:
        await page.evaluate(_SETTLE_JS, timeout)
    except PlaywrightTimeoutError:
        pass  # hard timeout inside JS already resolved — this is expected
    except Exception as e:
        logger.warning("safe_wait JS failed (%s), falling back to load-state.", e)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=timeout)
        except PlaywrightTimeoutError:
            logger.warning("safe_wait: domcontentloaded timed out, continuing.")
