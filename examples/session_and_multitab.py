"""
llmbrowser — session persistence & multi-tab browsing
=======================================================

Session persistence
-------------------
Save and restore browser state (cookies + localStorage) to a JSON file.
This lets you log in once and reuse the session across script runs, avoiding
repeated logins and CAPTCHA challenges.

Multi-tab browsing
------------------
Open, switch, and close tabs programmatically. Each tab is indexed from 0.
The agent can work across multiple tabs the same way a human would.

Usage
-----
    python examples/session_and_multitab.py
    python examples/session_and_multitab.py --session   # save/load demo
    python examples/session_and_multitab.py --tabs      # multi-tab demo
"""

import asyncio
import sys
import json
import tempfile
from pathlib import Path

from llmbrowser import LLMBrowser, BrowserToolkit


# ── 1. Save a session ─────────────────────────────────────────────────────────

async def save_session_demo() -> str:
    """
    Navigate somewhere, then save the browser session to a JSON file.

    The session file stores:
    - All cookies (including HttpOnly and Secure)
    - localStorage for every origin visited
    - sessionStorage (where supported)

    You can inspect the file with any JSON viewer to see what was captured.
    Returns the path to the saved file.
    """
    print("=== Save Session Demo ===\n")

    session_path = str(Path(tempfile.gettempdir()) / "llmbrowser_session.json")

    async with LLMBrowser(headless=True) as browser:
        await browser.navigate("https://example.com")

        # Set a cookie and some localStorage so there is something to save
        await browser._page.evaluate("""() => {
            document.cookie = 'demo_cookie=hello; path=/';
            localStorage.setItem('demo_key', 'demo_value');
        }""")

        await browser.save_session(session_path)
        print(f"Session saved to: {session_path}")

        # Peek at what was saved
        with open(session_path) as f:
            data = json.load(f)
        print(f"  cookies captured  : {len(data.get('cookies', []))}")
        print(f"  origins captured  : {len(data.get('origins', []))}")

    return session_path


# ── 2. Load a session ─────────────────────────────────────────────────────────

async def load_session_demo(session_path: str) -> None:
    """
    Restore a previously saved session.

    Option A — load after startup (demonstrated here):
        Useful when you want to start fresh and then restore state.

    Option B — load on startup via constructor:
        async with LLMBrowser(session_file="session.json") as browser:
            ...   # cookies & localStorage already restored

    Both options apply the session to a newly created browser context, so
    you inherit all cookies as if you had logged in manually.
    """
    print("=== Load Session Demo ===\n")

    async with LLMBrowser(headless=True) as browser:
        await browser.load_session(session_path)
        print(f"Session loaded from: {session_path}")

        # Navigate and verify the cookie survived
        await browser.navigate("https://example.com")
        cookies = await browser._page.evaluate("document.cookie")
        local_val = await browser._page.evaluate("localStorage.getItem('demo_key')")

        print(f"  document.cookie         : {cookies!r}")
        print(f"  localStorage['demo_key']: {local_val!r}")


# ── 3. Auto-load session on startup ───────────────────────────────────────────

async def auto_load_demo(session_path: str) -> None:
    """
    Pass session_file= to the constructor to restore state before the first
    navigate(). This is the typical pattern for long-running agents that
    need to stay logged in across restarts.
    """
    print("=== Auto-Load Session Demo ===\n")

    async with LLMBrowser(headless=True, session_file=session_path) as browser:
        # Session is already loaded — navigate directly without logging in
        await browser.navigate("https://example.com")
        print(f"  Arrived at   : {browser.current_url}")
        print(f"  Cookie check : {await browser._page.evaluate('document.cookie')!r}")


# ── 4. Multi-tab browsing ─────────────────────────────────────────────────────

async def multitab_demo() -> None:
    """
    Open multiple tabs, switch between them, and close them.

    Tab management methods:
        new_tab(url=None) → int     Open a new tab; returns its index.
        switch_tab(index)           Make the tab at index active.
        close_tab()                 Close the currently active tab.
        list_tabs() → list[dict]    Return metadata for all open tabs.
        browser.tab_count           Number of open tabs.
        browser.active_tab          Index of the currently active tab.

    Tabs are 0-indexed. Attempting to close the last remaining tab raises
    ActionExecutionError to prevent the browser session from becoming invalid.
    """
    print("=== Multi-Tab Demo ===\n")

    async with LLMBrowser(headless=True) as browser:
        # Start: one tab open
        print(f"Initial tab count : {browser.tab_count}")

        # Open a second tab and navigate it to a URL
        idx_a = await browser.new_tab("https://example.com")
        print(f"Opened tab {idx_a}  : {browser.current_url}  (active)")

        # Open a third tab without a URL — starts at about:blank
        idx_b = await browser.new_tab()
        print(f"Opened tab {idx_b}  : about:blank            (active)")

        print(f"Total tabs now    : {browser.tab_count}\n")

        # List all tabs
        tabs = await browser.list_tabs()
        for t in tabs:
            active_marker = "<-- active" if t["active"] else ""
            print(f"  [{t['index']}] {t['url']:<40} {active_marker}")

        # Switch back to tab 0 (the original tab)
        await browser.switch_tab(0)
        print(f"\nSwitched to tab 0 : active_tab = {browser.active_tab}")

        # Switch to the example.com tab and read its title
        await browser.switch_tab(idx_a)
        title = await browser.page_title()
        print(f"Switched to tab {idx_a} : title = {title!r}")

        # Close the active tab (idx_a) — switches to the previous tab
        await browser.close_tab()
        print(f"\nClosed tab {idx_a}   : tab_count = {browser.tab_count}")


# ── 5. Agent loop across multiple tabs ────────────────────────────────────────

async def multitab_toolkit_demo() -> None:
    """
    Use BrowserToolkit's new_tab, switch_tab, list_tabs, close_tab tools
    within a normal agent loop.

    These tools are included in all 16 base tools and work with every
    framework adapter (Anthropic, OpenAI, Google, Bedrock, etc.).
    """
    print("=== Multi-Tab Toolkit Demo ===\n")

    async with LLMBrowser(headless=True) as browser:
        toolkit = BrowserToolkit(browser)

        # Navigate in tab 0
        print(await toolkit.execute("navigate", url="https://example.com"))

        # Open a second tab
        result = await toolkit.execute("new_tab", url="https://iana.org")
        print(result)

        # List all open tabs
        result = await toolkit.execute("list_tabs")
        print(result)

        # Switch back to tab 0
        result = await toolkit.execute("switch_tab", index=0)
        print(result)

        # Close tab 1 (switch to it first)
        result = await toolkit.execute("switch_tab", index=1)
        result = await toolkit.execute("close_tab")
        print(result)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--session" in sys.argv:
        session_path = asyncio.run(save_session_demo())
        print()
        asyncio.run(load_session_demo(session_path))
        print()
        asyncio.run(auto_load_demo(session_path))
    elif "--tabs" in sys.argv:
        asyncio.run(multitab_demo())
        print()
        asyncio.run(multitab_toolkit_demo())
    else:
        # Run everything
        session_path = asyncio.run(save_session_demo())
        print()
        asyncio.run(load_session_demo(session_path))
        print()
        asyncio.run(multitab_demo())
