import sys
sys.dont_write_bytecode = True

"""
Shared fixtures for the llmbrowser test suite.

Browser tests use a single Chromium instance for the whole session to keep
startup costs low.  asyncio_default_fixture_loop_scope = "session" in
pyproject.toml ensures all async fixtures share the same event loop.
"""

import pytest

from llmbrowser import LLMBrowser


# ── Browser instance ──────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
async def browser():
    """
    A single headless Chromium instance shared across all browser tests.

    dismiss_dialogs=False so cookie-banner tests can control it explicitly.
    stealth=False so stealth-specific tests can spin up their own instance.
    """
    async with LLMBrowser(
        browser="chromium",
        headless=True,
        dismiss_dialogs=False,
        stealth=False,
    ) as b:
        yield b


# ── HTML helper ───────────────────────────────────────────────────────────────

async def load_html(browser: LLMBrowser, html: str) -> None:
    """
    Set page content directly — no network, no encoding.
    Resets scroll to (0, 0) so position-sensitive tests start clean.

    Retries up to 3 times with a short delay in case the previous test left
    the page in an unstable state (e.g. chrome-error retry navigation).
    """
    import asyncio

    for attempt in range(3):
        try:
            await browser._page.set_content(html, wait_until="domcontentloaded")
            await browser._page.evaluate("window.scrollTo(0, 0)")
            return
        except Exception:
            if attempt == 2:
                raise
            await asyncio.sleep(0.5)


# ── Reusable HTML pages ───────────────────────────────────────────────────────

BASIC_HTML = """<!DOCTYPE html>
<html><body>
  <h1>Test Page</h1>
  <button id="btn1">Click me</button>
  <input type="text" id="inp1" placeholder="Enter text" />
  <a href="https://example.com" id="link1">Go to example</a>
</body></html>"""

SCROLLABLE_HTML = """<!DOCTYPE html>
<html><body style="height:3000px; margin:0; padding:20px;">
  <p id="top">Top of page</p>
  <button id="top-btn">Top button</button>
  <div style="margin-top:2500px">
    <p id="bottom">Bottom of page</p>
    <button id="bottom-btn">Bottom button</button>
  </div>
</body></html>"""

RECAPTCHA_V2_HTML = """<!DOCTYPE html>
<html><body>
  <h1>reCAPTCHA v2 Test</h1>
  <div class="g-recaptcha"
       data-sitekey="6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI"
       data-callback="onSolved"></div>
  <textarea id="g-recaptcha-response" style="display:none"></textarea>
  <script>
    window._captchaToken = null;
    function onSolved(token) { window._captchaToken = token; }
  </script>
</body></html>"""

HCAPTCHA_HTML = """<!DOCTYPE html>
<html><body>
  <h1>hCaptcha Test</h1>
  <div class="h-captcha"
       data-sitekey="10000000-ffff-ffff-ffff-000000000001"
       data-callback="onHSolved"></div>
  <textarea name="h-captcha-response" style="display:none"></textarea>
  <script>
    window._hToken = null;
    function onHSolved(token) { window._hToken = token; }
  </script>
</body></html>"""
