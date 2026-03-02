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


# ── New tools — navigation helpers ───────────────────────────────────────────

class TestGoForwardReload:
    async def test_go_forward(self, browser):
        # Use two real navigations so history is clean (no chrome-error bleed)
        await browser.navigate("https://example.com")
        await browser.navigate("https://www.iana.org/domains/reserved")
        await browser.go_back()
        await browser.go_forward()
        assert "iana.org" in browser.current_url

    async def test_reload_resets_js_state(self, browser):
        await browser.navigate("https://example.com")
        original_title = await browser._page.title()
        await browser._page.evaluate("document.title = 'mutated'")
        assert await browser._page.title() == "mutated"
        await browser.reload()
        assert await browser._page.title() == original_title

    async def test_toolkit_go_forward(self, browser):
        await browser.navigate("https://example.com")
        await browser.navigate("https://www.iana.org/domains/reserved")
        tk = BrowserToolkit(browser)
        await tk.execute("go_back")
        result = await tk.execute("go_forward")
        assert "URL:" in result

    async def test_toolkit_reload_page(self, browser):
        await browser.navigate("https://example.com")
        tk = BrowserToolkit(browser)
        result = await tk.execute("reload_page")
        assert "URL:" in result


# ── New tools — hover & select ────────────────────────────────────────────────

class TestHoverAndSelect:
    async def test_hover_element_fires_mouseover(self, browser):
        html = """<!DOCTYPE html><html><body>
            <button onmouseover="document.title='hovered'">Hover me</button>
        </body></html>"""
        await load_html(browser, html)
        state = await browser.get_state()
        btn = next(e for e in state.interactive_elements if e["tag"] == "button")
        await browser.hover_element(btn["id"])
        assert await browser._page.title() == "hovered"

    async def test_hover_returns_page_state(self, browser):
        html = """<!DOCTYPE html><html><body>
            <button onmouseover="document.title='hovered'">Hover me</button>
        </body></html>"""
        await load_html(browser, html)
        tk = BrowserToolkit(browser)
        state = await browser.get_state()
        btn = next(e for e in state.interactive_elements if e["tag"] == "button")
        result = await tk.execute("hover_element", element_id=btn["id"])
        assert "URL:" in result

    async def test_select_option_by_value(self, browser):
        html = """<!DOCTYPE html><html><body>
            <select id="color">
              <option value="red">Red</option>
              <option value="green">Green</option>
              <option value="blue">Blue</option>
            </select>
        </body></html>"""
        await load_html(browser, html)
        state = await browser.get_state()
        sel = next(e for e in state.interactive_elements if e["tag"] == "select")
        selected = await browser.select_option(sel["id"], value="green")
        assert selected == "green"
        val = await browser._page.eval_on_selector("#color", "el => el.value")
        assert val == "green"

    async def test_select_option_by_label(self, browser):
        html = """<!DOCTYPE html><html><body>
            <select id="fruit">
              <option value="a">Apple</option>
              <option value="b">Banana</option>
            </select>
        </body></html>"""
        await load_html(browser, html)
        state = await browser.get_state()
        sel = next(e for e in state.interactive_elements if e["tag"] == "select")
        selected = await browser.select_option(sel["id"], label="Banana")
        assert selected == "b"

    async def test_select_option_by_index(self, browser):
        html = """<!DOCTYPE html><html><body>
            <select id="num">
              <option value="one">One</option>
              <option value="two">Two</option>
              <option value="three">Three</option>
            </select>
        </body></html>"""
        await load_html(browser, html)
        state = await browser.get_state()
        sel = next(e for e in state.interactive_elements if e["tag"] == "select")
        selected = await browser.select_option(sel["id"], index=2)
        assert selected == "three"

    async def test_select_no_selector_raises(self, browser):
        html = """<!DOCTYPE html><html><body>
            <select id="s"><option value="x">X</option></select>
        </body></html>"""
        await load_html(browser, html)
        state = await browser.get_state()
        sel = next(e for e in state.interactive_elements if e["tag"] == "select")
        with pytest.raises(ValueError, match="Provide one of"):
            await browser.select_option(sel["id"])

    async def test_toolkit_select_option(self, browser):
        html = """<!DOCTYPE html><html><body>
            <select id="s">
              <option value="x">X</option>
              <option value="y">Y</option>
            </select>
        </body></html>"""
        await load_html(browser, html)
        tk = BrowserToolkit(browser)
        state = await browser.get_state()
        sel = next(e for e in state.interactive_elements if e["tag"] == "select")
        result = await tk.execute("select_option", element_id=sel["id"], value="y")
        assert "Selected: y" in result


# ── New tools — storage ───────────────────────────────────────────────────────

class TestManageStorage:
    async def test_localstorage_set_and_get(self, browser):
        # Navigate first so the page is at a real URL where localStorage is available
        await browser.navigate("https://example.com")
        await load_html(browser, BASIC_HTML)
        await browser.manage_storage("local", "set", key="foo", value="bar")
        result = await browser.manage_storage("local", "get", key="foo")
        assert result == "bar"

    async def test_localstorage_get_missing_key(self, browser):
        await load_html(browser, BASIC_HTML)
        result = await browser.manage_storage("local", "get", key="__no_such_key__")
        assert result == "(not set)"

    async def test_localstorage_remove(self, browser):
        await load_html(browser, BASIC_HTML)
        await browser.manage_storage("local", "set", key="temp", value="val")
        await browser.manage_storage("local", "remove", key="temp")
        result = await browser.manage_storage("local", "get", key="temp")
        assert result == "(not set)"

    async def test_localstorage_getall(self, browser):
        await load_html(browser, BASIC_HTML)
        await browser.manage_storage("local", "clear")
        await browser.manage_storage("local", "set", key="k1", value="v1")
        await browser.manage_storage("local", "set", key="k2", value="v2")
        result = await browser.manage_storage("local", "getall")
        import json
        data = json.loads(result)
        assert data["k1"] == "v1"
        assert data["k2"] == "v2"

    async def test_localstorage_clear(self, browser):
        await load_html(browser, BASIC_HTML)
        await browser.manage_storage("local", "set", key="x", value="1")
        await browser.manage_storage("local", "clear")
        result = await browser.manage_storage("local", "getall")
        import json
        assert json.loads(result) == {}

    async def test_sessionstorage_set_and_get(self, browser):
        await load_html(browser, BASIC_HTML)
        await browser.manage_storage("session", "set", key="sess_key", value="sess_val")
        result = await browser.manage_storage("session", "get", key="sess_key")
        assert result == "sess_val"

    async def test_toolkit_manage_storage(self, browser):
        await load_html(browser, BASIC_HTML)
        tk = BrowserToolkit(browser)
        await tk.execute("manage_storage", store="local", action="set", key="tk_key", value="tk_val")
        result = await tk.execute("manage_storage", store="local", action="get", key="tk_key")
        assert "tk_val" in result


# ── New tools — cookies ───────────────────────────────────────────────────────

class TestManageCookies:
    async def test_set_and_list_cookie(self, browser):
        # Cookies require a real URL origin — set_content leaves page at about:blank
        await browser.navigate("https://example.com")
        await browser._context.clear_cookies()
        await browser.manage_cookies("set", name="test_cookie", value="cookie_val")
        result = await browser.manage_cookies("list")
        assert "test_cookie" in result
        assert "cookie_val" in result

    async def test_delete_cookie(self, browser):
        await browser.navigate("https://example.com")
        await browser.manage_cookies("set", name="del_me", value="gone")
        await browser.manage_cookies("delete", name="del_me")
        result = await browser.manage_cookies("list")
        assert "del_me" not in result

    async def test_list_cookies_empty(self, browser):
        await browser.navigate("https://example.com")
        await browser._context.clear_cookies()
        result = await browser.manage_cookies("list")
        assert result == "No cookies set."

    async def test_toolkit_manage_cookies(self, browser):
        await browser.navigate("https://example.com")
        await browser._context.clear_cookies()
        tk = BrowserToolkit(browser)
        await tk.execute("manage_cookies", action="set", name="tk_cookie", value="tk_val")
        result = await tk.execute("manage_cookies", action="list")
        assert "tk_cookie" in result


# ── New tools — execute_script ────────────────────────────────────────────────

class TestExecuteScript:
    async def test_returns_string_result(self, browser):
        await load_html(browser, BASIC_HTML)
        result = await browser.execute_script("() => document.title")
        assert isinstance(result, str)

    async def test_returns_no_return_value_sentinel(self, browser):
        await load_html(browser, BASIC_HTML)
        result = await browser.execute_script("() => { /* no return */ }")
        assert result == "(no return value)"

    async def test_can_read_dom(self, browser):
        html = """<!DOCTYPE html><html><body>
            <p id="target">hello script</p>
        </body></html>"""
        await load_html(browser, html)
        result = await browser.execute_script(
            "() => document.getElementById('target').textContent"
        )
        assert result == "hello script"

    async def test_can_mutate_dom(self, browser):
        await load_html(browser, BASIC_HTML)
        await browser.execute_script("() => { document.title = 'script_title'; }")
        title = await browser._page.title()
        assert title == "script_title"

    async def test_toolkit_execute_script(self, browser):
        await load_html(browser, BASIC_HTML)
        tk = BrowserToolkit(browser)
        result = await tk.execute("execute_script", script="() => 'hello from js'")
        assert "hello from js" in result


# ── New tools — get_element_attribute ────────────────────────────────────────

class TestGetElementAttribute:
    async def test_reads_href(self, browser):
        await load_html(browser, BASIC_HTML)
        state = await browser.get_state()
        link = next(e for e in state.interactive_elements if e["tag"] == "a")
        result = await browser.get_element_attribute(link["id"], "href")
        assert "example.com" in result

    async def test_reads_data_attribute(self, browser):
        html = """<!DOCTYPE html><html><body>
            <button data-action="submit" data-id="42">Go</button>
        </body></html>"""
        await load_html(browser, html)
        state = await browser.get_state()
        btn = next(e for e in state.interactive_elements if e["tag"] == "button")
        assert await browser.get_element_attribute(btn["id"], "data-action") == "submit"
        assert await browser.get_element_attribute(btn["id"], "data-id") == "42"

    async def test_missing_attribute_returns_sentinel(self, browser):
        await load_html(browser, BASIC_HTML)
        state = await browser.get_state()
        btn = next(e for e in state.interactive_elements if e["tag"] == "button")
        result = await browser.get_element_attribute(btn["id"], "nonexistent-attr")
        assert "not found" in result

    async def test_toolkit_get_element_attribute(self, browser):
        await load_html(browser, BASIC_HTML)
        tk = BrowserToolkit(browser)
        state = await browser.get_state()
        link = next(e for e in state.interactive_elements if e["tag"] == "a")
        result = await tk.execute("get_element_attribute", element_id=link["id"], attribute="href")
        assert "example.com" in result


# ── New tools — handle_dialog ─────────────────────────────────────────────────

class TestHandleDialog:
    async def test_accept_alert(self, browser):
        # Use CSS id selector — get_state() runs CLEANUP_JS which removes data-llm-id
        html = """<!DOCTYPE html><html><body>
            <button id="btn" onclick="alert('hello'); document.title='after_alert'">Alert</button>
        </body></html>"""
        await load_html(browser, html)
        browser.handle_next_dialog("accept")
        await browser._page.click("#btn")
        await browser._page.wait_for_timeout(300)
        assert await browser._page.title() == "after_alert"

    async def test_dismiss_confirm(self, browser):
        html = """<!DOCTYPE html><html><body>
            <button id="btn" onclick="document.title = confirm('sure?') ? 'yes' : 'no'">Confirm</button>
        </body></html>"""
        await load_html(browser, html)
        browser.handle_next_dialog("dismiss")
        await browser._page.click("#btn")
        await browser._page.wait_for_timeout(300)
        assert await browser._page.title() == "no"

    async def test_accept_confirm(self, browser):
        html = """<!DOCTYPE html><html><body>
            <button id="btn" onclick="document.title = confirm('sure?') ? 'yes' : 'no'">Confirm</button>
        </body></html>"""
        await load_html(browser, html)
        browser.handle_next_dialog("accept")
        await browser._page.click("#btn")
        await browser._page.wait_for_timeout(300)
        assert await browser._page.title() == "yes"

    async def test_accept_prompt_with_text(self, browser):
        html = """<!DOCTYPE html><html><body>
            <button id="btn" onclick="document.title = prompt('name?')">Prompt</button>
        </body></html>"""
        await load_html(browser, html)
        browser.handle_next_dialog("accept", prompt_text="Alice")
        await browser._page.click("#btn")
        await browser._page.wait_for_timeout(300)
        assert await browser._page.title() == "Alice"

    async def test_toolkit_handle_dialog(self, browser):
        await load_html(browser, BASIC_HTML)
        tk = BrowserToolkit(browser)
        result = await tk.execute("handle_dialog", action="accept")
        assert "accept" in result


# ── New tools — drag_and_drop ─────────────────────────────────────────────────

class TestDragAndDrop:
    async def test_drag_and_drop_fires_events(self, browser):
        # Use <button> elements — always annotated by ANNOTATION_JS
        html = """<!DOCTYPE html><html><body>
            <button id="src" draggable="true"
                    ondragstart="window._dragged=true"
                    style="width:80px;height:40px">drag</button>
            <button id="tgt"
                    ondrop="document.title='dropped'"
                    ondragover="event.preventDefault()"
                    style="width:80px;height:40px;margin-top:20px">drop</button>
        </body></html>"""
        await load_html(browser, html)
        state = await browser.get_state()
        src = next(e for e in state.interactive_elements if e.get("text") == "drag")
        tgt = next(e for e in state.interactive_elements if e.get("text") == "drop")
        await browser.drag_and_drop(src["id"], tgt["id"])
        assert await browser._page.title() == "dropped"

    async def test_toolkit_drag_and_drop(self, browser):
        html = """<!DOCTYPE html><html><body>
            <button id="src" draggable="true"
                    ondragstart="window._d=1"
                    style="width:80px;height:40px">A</button>
            <button id="tgt"
                    ondrop="document.title='ok'"
                    ondragover="event.preventDefault()"
                    style="width:80px;height:40px;margin-top:20px">B</button>
        </body></html>"""
        await load_html(browser, html)
        tk = BrowserToolkit(browser)
        state = await browser.get_state()
        src = next(e for e in state.interactive_elements if e.get("text") == "A")
        tgt = next(e for e in state.interactive_elements if e.get("text") == "B")
        result = await tk.execute("drag_and_drop", source_id=src["id"], target_id=tgt["id"])
        assert "URL:" in result


# ── New tools — take_element_screenshot ──────────────────────────────────────

class TestTakeElementScreenshot:
    async def test_returns_base64_png(self, browser):
        await load_html(browser, BASIC_HTML)
        state = await browser.get_state()
        btn = next(e for e in state.interactive_elements if e["tag"] == "button")
        b64 = await browser.take_element_screenshot(btn["id"])
        import base64
        # Must be valid base64 that decodes to a PNG
        raw = base64.b64decode(b64)
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"

    async def test_toolkit_returns_summary(self, browser):
        await load_html(browser, BASIC_HTML)
        tk = BrowserToolkit(browser)
        state = await browser.get_state()
        btn = next(e for e in state.interactive_elements if e["tag"] == "button")
        result = await tk.execute("take_element_screenshot", element_id=btn["id"])
        assert "base64 PNG" in result


# ── New tools — switch_frame ──────────────────────────────────────────────────

class TestSwitchFrame:
    async def test_lists_main_frame(self, browser):
        await load_html(browser, BASIC_HTML)
        result = await browser.switch_frame()
        assert "FRAMES ON PAGE" in result
        assert "main" in result

    async def test_lists_iframes(self, browser):
        html = """<!DOCTYPE html><html><body>
            <iframe src="about:blank" name="inner"></iframe>
        </body></html>"""
        await load_html(browser, html)
        result = await browser.switch_frame()
        assert "2 total" in result

    async def test_toolkit_switch_frame(self, browser):
        await load_html(browser, BASIC_HTML)
        tk = BrowserToolkit(browser)
        result = await tk.execute("switch_frame")
        assert "FRAMES ON PAGE" in result


# ── New tools — set_geolocation ───────────────────────────────────────────────

class TestSetGeolocation:
    async def test_geolocation_reported_correctly(self, browser):
        await load_html(browser, BASIC_HTML)
        await browser.set_geolocation(37.7749, -122.4194)
        coords = await browser._page.evaluate("""() =>
            new Promise(resolve =>
                navigator.geolocation.getCurrentPosition(
                    p => resolve({lat: p.coords.latitude, lon: p.coords.longitude}),
                    () => resolve(null)
                )
            )
        """)
        assert coords is not None
        assert abs(coords["lat"] - 37.7749) < 0.001
        assert abs(coords["lon"] - (-122.4194)) < 0.001

    async def test_toolkit_set_geolocation(self, browser):
        await load_html(browser, BASIC_HTML)
        tk = BrowserToolkit(browser)
        result = await tk.execute("set_geolocation", latitude=51.5074, longitude=-0.1278)
        assert "51.5074" in result
        assert "-0.1278" in result


# ── New tools — export_pdf ────────────────────────────────────────────────────

class TestExportPdf:
    async def test_creates_pdf_file(self, browser, tmp_path):
        await browser.navigate("https://example.com")
        path = str(tmp_path / "page.pdf")
        saved = await browser.export_pdf(path=path)
        import os
        assert saved == path
        assert os.path.exists(path)
        assert os.path.getsize(path) > 1000   # non-trivial PDF

    async def test_default_path_in_download_dir(self, browser):
        await browser.navigate("https://example.com")
        saved = await browser.export_pdf()
        import os
        assert saved.endswith(".pdf")
        assert os.path.exists(saved)

    async def test_toolkit_export_pdf(self, browser, tmp_path):
        await browser.navigate("https://example.com")
        tk = BrowserToolkit(browser)
        path = str(tmp_path / "tk.pdf")
        result = await tk.execute("export_pdf", path=path)
        assert "PDF saved to:" in result
        assert path in result
