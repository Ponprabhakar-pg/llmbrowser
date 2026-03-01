"""
python -m llmbrowser install
==============================

Installs Playwright browser binaries so llmbrowser works out of the box.

Usage
-----
    python -m llmbrowser install              # installs chromium (default)
    python -m llmbrowser install chromium     # same
    python -m llmbrowser install firefox
    python -m llmbrowser install webkit
    python -m llmbrowser install all          # chromium + firefox + webkit

This is the same as running `playwright install <browser>` manually, but
integrated so you only need to remember one command after `pip install llmbrowser`.
"""

from __future__ import annotations

import subprocess
import sys


_VALID_BROWSERS = {"chromium", "firefox", "webkit"}


def _install(browsers: list[str]) -> int:
    for browser in browsers:
        print(f"Installing Playwright browser: {browser} ...")
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", browser],
            check=False,
        )
        if result.returncode != 0:
            print(f"ERROR: Failed to install '{browser}'.", file=sys.stderr)
            print(f"Try running manually:  playwright install {browser}", file=sys.stderr)
            return result.returncode
        print(f"  ✓ {browser} installed.\n")
    return 0


def main() -> int:
    args = sys.argv[1:]

    if not args or args[0] != "install":
        print(__doc__)
        print("Commands:")
        print("  install [browser]   Install Playwright browser binaries")
        print("                      browser: chromium (default), firefox, webkit, all")
        return 0

    targets = args[1:]

    if not targets or targets == ["chromium"]:
        return _install(["chromium"])

    if targets == ["all"]:
        return _install(["chromium", "firefox", "webkit"])

    unknown = set(targets) - _VALID_BROWSERS
    if unknown:
        print(f"ERROR: Unknown browser(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"Valid options: chromium, firefox, webkit, all", file=sys.stderr)
        return 1

    return _install(targets)


if __name__ == "__main__":
    sys.exit(main())
