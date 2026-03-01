"""
Auto-install Playwright's Chromium on first import.

Runs synchronously (subprocess) so it completes before any user code executes.
Uses a sentinel file to skip the check on subsequent imports — zero overhead
after the first successful install.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Sentinel file written after a successful install — skips the check next time.
_SENTINEL = Path(__file__).parent / ".chromium_installed"


def _ensure_chromium_installed() -> None:
    """
    Install Playwright's Chromium binary if it isn't present yet.

    Called automatically when llmbrowser is first imported. Subsequent imports
    hit the sentinel file check and return immediately with no overhead.
    Users who prefer manual control can set the env var:
        LLMBROWSER_NO_AUTO_INSTALL=1
    """
    import os
    if os.environ.get("LLMBROWSER_NO_AUTO_INSTALL"):
        return

    if _SENTINEL.exists():
        return

    # Ask Playwright where Chromium lives — no network call, just a path check.
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            executable = p.chromium.executable_path
        if Path(executable).exists():
            _SENTINEL.touch()
            return
    except Exception:
        pass  # Playwright not importable or path check failed — fall through to install

    # Binary is missing — install it now.
    print(
        "[llmbrowser] Chromium not found. Installing Playwright browser binaries "
        "(one-time setup, ~150 MB)..."
    )
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        _SENTINEL.touch()
        print("[llmbrowser] Chromium installed successfully.")
    else:
        # Don't crash on import — just warn. The error will surface when the
        # user tries to open a browser.
        logger.warning(
            "llmbrowser: Chromium auto-install failed.\n"
            "Run  `llmbrowser install`  to install manually.\n"
            "Error: %s",
            result.stderr.strip(),
        )
