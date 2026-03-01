"""
llmbrowser — stealth mode, proxy & remote CDP
===============================================

Stealth mode patches the most common browser-fingerprinting signals so that
bot-detection systems (Cloudflare, DataDome, PerimeterX, etc.) cannot tell
that Playwright is driving the browser.

Proxy support lets you route traffic through HTTP/HTTPS or SOCKS5 proxies,
with optional username/password credentials.

Usage
-----
    python examples/stealth_and_proxy.py
    python examples/stealth_and_proxy.py --proxy http://user:pass@proxy.example.com:8080
    python examples/stealth_and_proxy.py --cdp wss://connect.browserbase.com?apiKey=KEY
"""

import asyncio
import sys

from llmbrowser import LLMBrowser


# ── 1. Stealth mode ───────────────────────────────────────────────────────────

async def stealth_demo() -> None:
    """
    Launch a browser with stealth=True and verify that the key fingerprinting
    signals have been patched.

    What stealth mode does
    ----------------------
    - navigator.webdriver      → undefined  (biggest automation tell)
    - window.chrome            → fake Chrome object (headless Chromium lacks it)
    - navigator.languages      → ["en-US", "en"]
    - navigator.plugins        → fake PDF viewer plugins
    - navigator.permissions    → returns fake result for notification query
    - WebGL UNMASKED_VENDOR    → "Intel Inc."
    - WebGL UNMASKED_RENDERER  → "Intel Iris OpenGL Engine"
    - Canvas toDataURL()       → imperceptible per-run noise (defeats canvas hash)

    The patches are injected via Playwright's add_init_script() so they run
    *before* any page JavaScript — nothing on the page can observe the
    un-patched values.

    Note: stealth=False still suppresses navigator.webdriver because
    --disable-blink-features=AutomationControlled is always in the Chromium
    launch args. Stealth adds the remaining signal patches on top.
    """
    print("=== Stealth Demo ===\n")

    async with LLMBrowser(headless=True, stealth=True) as browser:
        await browser.navigate("https://example.com")

        # Read the patched values directly from the page
        webdriver = await browser._page.evaluate("navigator.webdriver")
        languages = await browser._page.evaluate("navigator.languages")
        webgl_vendor = await browser._page.evaluate("""() => {
            const c = document.createElement('canvas');
            const gl = c.getContext('webgl');
            if (!gl) return 'WebGL unavailable';
            const ext = gl.getExtension('WEBGL_debug_renderer_info');
            return ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : 'extension unavailable';
        }""")

        print(f"navigator.webdriver : {webdriver!r}   (should be None/undefined)")
        print(f"navigator.languages : {languages}   (should contain 'en-US')")
        print(f"WebGL vendor        : {webgl_vendor!r}   (should be 'Intel Inc.')")


# ── 2. Proxy configuration ────────────────────────────────────────────────────

async def proxy_demo(proxy_url: str) -> None:
    """
    Route all browser traffic through a proxy.

    Accepted formats
    ----------------
    String URL (recommended):
        "http://proxy.example.com:8080"
        "http://username:password@proxy.example.com:8080"
        "socks5://proxy.example.com:1080"

    Playwright dict (advanced):
        {
            "server":   "http://proxy.example.com:8080",
            "username": "user",        # optional
            "password": "pass",        # optional
            "bypass":   "localhost",   # optional comma-separated bypass list
        }

    The proxy is applied at the browser-context level, so it automatically
    applies to every page, tab, and loaded resource.
    """
    print(f"=== Proxy Demo (proxy={proxy_url}) ===\n")

    async with LLMBrowser(headless=True, proxy=proxy_url) as browser:
        try:
            await browser.navigate("https://example.com")
            print(f"Navigated via proxy to: {browser.current_url}")
        except Exception as e:
            # Expected if the proxy isn't reachable in this environment
            print(f"Proxy navigation failed (expected in demo): {e}")


# ── 3. Stealth + proxy combined ───────────────────────────────────────────────

async def stealth_and_proxy_demo(proxy_url: str) -> None:
    """
    Use stealth mode and a proxy together.

    This is the typical setup for scraping tasks that need both evasion and
    IP rotation. All constructor parameters compose freely.
    """
    print("=== Stealth + Proxy Demo ===\n")

    async with LLMBrowser(
        headless=True,
        stealth=True,
        proxy=proxy_url,
        # Optionally pair with a realistic user-agent string:
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    ) as browser:
        try:
            await browser.navigate("https://example.com")
            print(f"Reached: {browser.current_url}")
        except Exception as e:
            print(f"Proxy navigation failed (expected in demo): {e}")


# ── 4. Remote CDP ─────────────────────────────────────────────────────────────

async def remote_cdp_demo(cdp_url: str) -> None:
    """
    Connect to a browser running on a managed service or remote machine.

    Instead of launching a local browser, llmbrowser connects to an already-
    running Chromium via its Chrome DevTools Protocol WebSocket endpoint.

    Supported services (pass their CDP URL):
        Browserbase  — wss://connect.browserbase.com?apiKey=YOUR_KEY
        Anchor       — wss://browser.anchor.dev/cdp?token=YOUR_TOKEN
        Self-hosted  — ws://your-vps:9222
                       (start Chromium with --remote-debugging-port=9222)

    What still works:   stealth, session_file, viewport, user_agent,
                        on_captcha_detected, all 16 tools, HITL, multi-tab.
    What is ignored:    browser=, headless=, slow_mo=, proxy=
                        (a warning is logged if proxy= is also passed).
    """
    print(f"=== Remote CDP Demo (url={cdp_url}) ===\n")

    async with LLMBrowser(remote_cdp_url=cdp_url) as browser:
        try:
            await browser.navigate("https://example.com")
            print(f"Connected and navigated to: {browser.current_url}")
            state = await browser.get_state(mode="aria")
            print(f"Page title : {state.title}")
            print(f"Elements   : {len(state.interactive_elements)}")
        except Exception as e:
            print(f"Remote connection failed (expected if URL is a placeholder): {e}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    proxy = next(
        (arg for arg in sys.argv[1:] if arg.startswith("http") or arg.startswith("socks")),
        "http://proxy.example.com:8080",   # placeholder — replace with a real proxy
    )
    cdp_url = next(
        (sys.argv[sys.argv.index("--cdp") + 1] for _ in [None] if "--cdp" in sys.argv),
        "wss://connect.browserbase.com?apiKey=PLACEHOLDER",
    )

    asyncio.run(stealth_demo())

    if "--proxy" in sys.argv:
        asyncio.run(proxy_demo(proxy))
        asyncio.run(stealth_and_proxy_demo(proxy))
    elif "--cdp" in sys.argv:
        asyncio.run(remote_cdp_demo(cdp_url))
    else:
        print("\nSkipping proxy/CDP demos.")
        print("  --proxy <url>  to test proxy routing")
        print("  --cdp   <url>  to test remote browser connection")
