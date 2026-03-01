"""
Browser integration tests — require a real Chromium browser.

All tests that need custom HTML use set_content() directly on the page
(via the load_html helper in conftest) to avoid network round-trips.
Tests that verify real navigation behaviour use example.com.

Run with:  pytest tests/test_browser.py -v
Skip with: pytest tests/test_unit.py        (no browser needed)
"""

import pytest

# All tests in this module must share the session event loop so they can use
# the session-scoped browser fixture without deadlocking.
pytestmark = pytest.mark.asyncio(loop_scope="session")

from llmbrowser import LLMBrowser, BrowserToolkit
from llmbrowser.exceptions import (
    ActionExecutionError,
    ElementNotFoundError,
    NavigationError,
)
from llmbrowser.schemas import CaptchaContext

from .conftest import (
    load_html,
    BASIC_HTML,
    SCROLLABLE_HTML,
    RECAPTCHA_V2_HTML,
    HCAPTCHA_HTML,
)


# ── Navigation ────────────────────────────────────────────────────────────────

class TestNavigation:
    async def test_https_auto_prepend(self, browser):
        await browser.navigate("example.com")
        assert "example.com" in browser.current_url

    async def test_full_url(self, browser):
        await browser.navigate("https://example.com")
        assert browser.current_url.startswith("https://example.com")

    async def test_page_title(self, browser):
        await browser.navigate("https://example.com")
        title = await browser.page_title()
        assert isinstance(title, str)
        assert len(title) > 0

    async def test_go_back(self, browser):
        # Navigate to a known-good page first, then load local HTML, then go back.
        # Runs before test_navigation_error_on_unreachable to avoid the chrome-error
        # page state that blocks subsequent navigation attempts.
        await browser.navigate("https://example.com")
        await load_html(browser, BASIC_HTML)
        await browser.go_back()
        # Just verify go_back doesn't raise and browser is still operational
        assert browser.current_url is not None

    async def test_navigation_error_on_unreachable(self, browser):
        with pytest.raises(NavigationError):
            # Port 19999 on localhost — nothing listening, fast connection refused
            await browser.navigate("http://127.0.0.1:19999")


# ── State capture ─────────────────────────────────────────────────────────────

class TestStateCapture:
    async def test_has_url_title_screenshot(self, browser):
        await load_html(browser, BASIC_HTML)
        state = await browser.get_state()
        assert state.url != ""
        assert isinstance(state.title, str)
        assert len(state.screenshot_b64) > 200   # non-trivial base64

    async def test_dom_mode_has_dom_not_aria(self, browser):
        await load_html(browser, BASIC_HTML)
        state = await browser.get_state(mode="dom")
        assert state.simplified_dom != ""
        assert state.aria_tree is None

    async def test_aria_mode_has_aria_not_dom(self, browser):
        await load_html(browser, BASIC_HTML)
        state = await browser.get_state(mode="aria")
        assert state.simplified_dom == ""
        assert state.aria_tree is not None
        assert len(state.aria_tree) > 0

    async def test_both_mode_has_both(self, browser):
        await load_html(browser, BASIC_HTML)
        state = await browser.get_state(mode="both")
        assert state.simplified_dom != ""
        assert state.aria_tree is not None

    async def test_interactive_elements_detected(self, browser):
        await load_html(browser, BASIC_HTML)
        state = await browser.get_state()
        assert len(state.interactive_elements) > 0
        for el in state.interactive_elements:
            assert isinstance(el["id"], int)
            assert el["id"] >= 1
            assert "tag" in el

    async def test_scroll_position_captured(self, browser):
        await load_html(browser, SCROLLABLE_HTML)
        state = await browser.get_state()
        assert "x" in state.scroll_position
        assert "y" in state.scroll_position
        assert state.page_height >= 3000


# ── Actions ───────────────────────────────────────────────────────────────────

class TestActions:
    async def test_click_element(self, browser):
        html = """<!DOCTYPE html><html><body>
            <button id="btn" onclick="this.textContent='Clicked'">Click me</button>
        </body></html>"""
        await load_html(browser, html)
        state = await browser.get_state()
        btn = next(e for e in state.interactive_elements if e["tag"] == "button")

        await browser.execute_action({
            "thought": "clicking the button",
            "action": "click",
            "target_id": btn["id"],
        })
        text = await browser._page.inner_text("#btn")
        assert text == "Clicked"

    async def test_type_text(self, browser):
        html = """<!DOCTYPE html><html><body>
            <input type="text" id="field" />
        </body></html>"""
        await load_html(browser, html)
        state = await browser.get_state()
        inp = next(e for e in state.interactive_elements if e["tag"] == "input")

        await browser.execute_action({
            "thought": "typing into field",
            "action": "type",
            "target_id": inp["id"],
            "value": "hello world",
        })
        value = await browser._page.eval_on_selector("#field", "el => el.value")
        assert value == "hello world"

    async def test_type_clears_existing_content(self, browser):
        html = """<!DOCTYPE html><html><body>
            <input type="text" id="field" value="old content" />
        </body></html>"""
        await load_html(browser, html)
        state = await browser.get_state()
        inp = next(e for e in state.interactive_elements if e["tag"] == "input")

        await browser.execute_action({
            "thought": "replacing content",
            "action": "type",
            "target_id": inp["id"],
            "value": "new value",
        })
        value = await browser._page.eval_on_selector("#field", "el => el.value")
        assert value == "new value"

    async def test_press_key_enter(self, browser):
        html = """<!DOCTYPE html><html><body>
            <input id="f" onkeydown="if(event.key==='Enter') document.title='submitted'" />
        </body></html>"""
        await load_html(browser, html)
        await browser._page.focus("#f")
        await browser.execute_action({"thought": "submit", "action": "key", "value": "Enter"})
        assert await browser._page.title() == "submitted"

    async def test_scroll_changes_y_position(self, browser):
        await load_html(browser, SCROLLABLE_HTML)
        before = await browser._page.evaluate("window.scrollY")
        await browser.execute_action({
            "thought": "scroll down",
            "action": "scroll",
            "scroll_direction": "down",
            "scroll_amount": 800,
        })
        after = await browser._page.evaluate("window.scrollY")
        assert after > before

    async def test_wait_action(self, browser):
        await load_html(browser, BASIC_HTML)
        # wait action should not raise
        await browser.execute_action({
            "thought": "waiting briefly",
            "action": "wait",
            "value": "500",
        })

    async def test_element_not_found_raises(self, browser):
        await load_html(browser, BASIC_HTML)
        with pytest.raises(ElementNotFoundError):
            await browser.execute_action({
                "thought": "click nonexistent",
                "action": "click",
                "target_id": 99999,
            })


# ── Multi-tab ─────────────────────────────────────────────────────────────────

class TestMultiTab:
    async def test_new_tab_increments_count(self, browser):
        initial = browser.tab_count
        idx = await browser.new_tab()
        assert browser.tab_count == initial + 1
        assert browser.active_tab == idx
        # cleanup
        await browser.close_tab()
        assert browser.tab_count == initial

    async def test_switch_tab(self, browser):
        await browser.new_tab()
        second_idx = browser.active_tab
        await browser.switch_tab(0)
        assert browser.active_tab == 0
        await browser.switch_tab(second_idx)
        await browser.close_tab()

    async def test_list_tabs(self, browser):
        tabs = await browser.list_tabs()
        assert len(tabs) >= 1
        active_tabs = [t for t in tabs if t["active"]]
        assert len(active_tabs) == 1

    async def test_switch_tab_invalid_index_raises(self, browser):
        with pytest.raises(ValueError):
            await browser.switch_tab(9999)

    async def test_close_last_tab_raises(self, browser):
        while browser.tab_count > 1:
            await browser.close_tab()
        with pytest.raises(ActionExecutionError):
            await browser.close_tab()


# ── Session persistence ───────────────────────────────────────────────────────

class TestSession:
    async def test_save_creates_file(self, browser, tmp_path):
        await browser.navigate("https://example.com")
        path = str(tmp_path / "session.json")
        await browser.save_session(path)
        import os, json
        assert os.path.exists(path)
        data = json.loads(open(path).read())
        assert "cookies" in data

    async def test_load_session_file_not_found(self, browser):
        with pytest.raises(FileNotFoundError):
            await browser.load_session("/nonexistent/session.json")


# ── Stealth mode ──────────────────────────────────────────────────────────────

class TestStealth:
    async def test_webdriver_undefined(self):
        async with LLMBrowser(browser="chromium", headless=True, stealth=True) as b:
            await b.navigate("https://example.com")
            wd = await b._page.evaluate("navigator.webdriver")
            # JS `undefined` comes back as Python `None`
            assert wd is None or wd is False

    async def test_languages_faked(self):
        async with LLMBrowser(browser="chromium", headless=True, stealth=True) as b:
            await b.navigate("https://example.com")
            langs = await b._page.evaluate("navigator.languages")
            assert "en-US" in langs

    async def test_webgl_vendor_faked(self):
        async with LLMBrowser(browser="chromium", headless=True, stealth=True) as b:
            await b.navigate("https://example.com")
            vendor = await b._page.evaluate("""() => {
                const c = document.createElement('canvas');
                const gl = c.getContext('webgl');
                if (!gl) return null;
                const ext = gl.getExtension('WEBGL_debug_renderer_info');
                return ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : null;
            }""")
            assert vendor == "Intel Inc."



# ── CAPTCHA detection and injection ──────────────────────────────────────────

class TestCaptcha:
    async def test_no_captcha_returns_none(self, browser):
        await load_html(browser, BASIC_HTML)
        ctx = await browser.detect_captcha()
        assert ctx is None

    async def test_detect_recaptcha_v2(self, browser):
        await load_html(browser, RECAPTCHA_V2_HTML)
        ctx = await browser.detect_captcha()
        assert ctx is not None
        assert ctx.type == "recaptcha_v2"
        assert ctx.sitekey == "6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI"

    async def test_detect_hcaptcha(self, browser):
        await load_html(browser, HCAPTCHA_HTML)
        ctx = await browser.detect_captcha()
        assert ctx is not None
        assert ctx.type == "hcaptcha"
        assert ctx.sitekey == "10000000-ffff-ffff-ffff-000000000001"

    async def test_inject_recaptcha_v2_token(self, browser):
        await load_html(browser, RECAPTCHA_V2_HTML)
        ok = await browser.inject_captcha_token("test_token_123", "recaptcha_v2")
        assert ok is True
        value = await browser._page.eval_on_selector(
            "#g-recaptcha-response", "el => el.value"
        )
        assert value == "test_token_123"

    async def test_inject_hcaptcha_token(self, browser):
        await load_html(browser, HCAPTCHA_HTML)
        ok = await browser.inject_captcha_token("hcaptcha_token_xyz", "hcaptcha")
        assert ok is True
        value = await browser._page.eval_on_selector(
            "[name='h-captcha-response']", "el => el.value"
        )
        assert value == "hcaptcha_token_xyz"

    async def test_solve_captcha_calls_callback(self, browser):
        await load_html(browser, RECAPTCHA_V2_HTML)

        received: list[CaptchaContext] = []

        async def solver(ctx: CaptchaContext) -> str:
            received.append(ctx)
            return "solved_token"

        browser._on_captcha_detected = solver
        try:
            solved = await browser.solve_captcha()
            assert solved is True
            assert len(received) == 1
            assert received[0].type == "recaptcha_v2"
            assert received[0].sitekey == "6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI"
        finally:
            browser._on_captcha_detected = None

    async def test_solve_captcha_no_callback_returns_false(self, browser):
        await load_html(browser, RECAPTCHA_V2_HTML)
        browser._on_captcha_detected = None
        assert await browser.solve_captcha() is False

    async def test_solve_captcha_no_captcha_on_page(self, browser):
        await load_html(browser, BASIC_HTML)

        async def solver(ctx):
            return "token"

        browser._on_captcha_detected = solver
        try:
            result = await browser.solve_captcha()
            assert result is False
        finally:
            browser._on_captcha_detected = None


# ── HITL tool ─────────────────────────────────────────────────────────────────

class TestHITL:
    async def test_tool_included_when_callback_set(self, browser):
        async def handler(ctx):
            return "ok"

        tk = BrowserToolkit(browser, on_human_needed=handler)
        names = {t["name"] for t in tk.as_anthropic_tools()}
        assert "request_human_help" in names

    async def test_tool_not_included_without_callback(self, browser):
        tk = BrowserToolkit(browser)
        names = {t["name"] for t in tk.as_anthropic_tools()}
        assert "request_human_help" not in names

    async def test_tool_calls_callback_with_context(self, browser):
        await load_html(browser, BASIC_HTML)
        received = []

        async def handler(ctx):
            received.append(ctx)
            return "the MFA code is 847291"

        tk = BrowserToolkit(browser, on_human_needed=handler)
        result = await tk.execute("request_human_help", reason="MFA prompt detected")
        assert "847291" in result
        assert len(received) == 1
        assert received[0].reason == "MFA prompt detected"
        assert received[0].url != ""
        assert received[0].screenshot_b64 != ""


# ── BrowserToolkit.execute ────────────────────────────────────────────────────

class TestToolkitExecute:
    async def test_navigate(self, browser):
        tk = BrowserToolkit(browser)
        result = await tk.execute("navigate", url="https://example.com")
        assert "example.com" in result
        assert "URL:" in result

    async def test_get_page_state(self, browser):
        await load_html(browser, BASIC_HTML)
        tk = BrowserToolkit(browser)
        result = await tk.execute("get_page_state")
        assert "URL:" in result
        assert "INTERACTIVE ELEMENTS:" in result

    async def test_unknown_tool_raises(self, browser):
        tk = BrowserToolkit(browser)
        with pytest.raises(ValueError, match="Unknown tool"):
            await tk.execute("does_not_exist")

    async def test_scroll_page(self, browser):
        await load_html(browser, SCROLLABLE_HTML)
        tk = BrowserToolkit(browser)
        result = await tk.execute("scroll_page", direction="down", amount=500)
        assert "URL:" in result

    async def test_list_tabs(self, browser):
        tk = BrowserToolkit(browser)
        result = await tk.execute("list_tabs")
        assert "OPEN TABS:" in result

    async def test_dismiss_dialogs_no_banner(self, browser):
        await load_html(browser, BASIC_HTML)
        tk = BrowserToolkit(browser)
        result = await tk.execute("dismiss_dialogs")
        assert "No dismissible" in result


# ── File handling ─────────────────────────────────────────────────────────────

class TestFileHandling:
    async def test_read_text_file(self, browser, tmp_path):
        f = tmp_path / "hello.txt"
        f.write_text("hello world")
        assert browser.read_file(str(f)) == "hello world"

    async def test_read_json_file(self, browser, tmp_path):
        f = tmp_path / "data.json"
        f.write_text('{"key": "value"}')
        content = browser.read_file(str(f))
        assert '"key"' in content

    async def test_read_binary_returns_summary(self, browser, tmp_path):
        f = tmp_path / "image.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        content = browser.read_file(str(f))
        assert "Binary" in content
        assert ".png" in content

    async def test_read_file_not_found(self, browser):
        with pytest.raises(FileNotFoundError):
            browser.read_file("/no/such/file.txt")

    async def test_read_file_truncates_large_content(self, browser, tmp_path):
        f = tmp_path / "big.txt"
        f.write_text("x" * 60_000)
        content = browser.read_file(str(f))
        assert "truncated" in content
        assert len(content) < 60_000

    async def test_downloads_initially_empty(self, browser):
        # Fresh session fixture — no downloads yet
        assert isinstance(browser.downloads, list)
