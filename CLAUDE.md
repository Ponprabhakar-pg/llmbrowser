# CLAUDE.md — llmbrowser

Reference file for Claude Code. Update this file whenever the codebase changes.

---

## Project Overview

**Package:** `llmbrowser` v0.2.0
**Purpose:** Framework-agnostic browser tool for AI agents (Playwright-based)
**Python:** ≥3.11
**Repo:** `https://github.com/Ponprabhakar-pg/llmbrowser`

---

## Directory Structure

```
llmbrowser/
├── __init__.py          # Public API exports
├── core.py              # LLMBrowser (~1602 lines) — main browser class
├── browser_tools.py     # BrowserToolkit (~1162 lines) — framework adapters
├── schemas.py           # Pydantic models + dataclasses
├── exceptions.py        # 5 custom exception classes
├── utils.py             # _SETTLE_JS, screenshot_to_base64, safe_wait
├── injector.py          # ANNOTATION_JS + CLEANUP_JS
├── dom_extractor.py     # DOM_EXTRACTION_JS + INTERACTIVE_ELEMENTS_JS
├── viewport.py          # Multi-viewport responsive QA runner (VIEWPORTS, ViewportResult, run_at_viewports)
├── _install.py          # Auto-install helper — runs on first import, installs Chromium if missing
└── __main__.py          # CLI entry point (`llmbrowser install`)

tests/
├── conftest.py          # Session browser fixture + load_html helper + HTML templates
├── test_unit.py         # Unit tests — no browser needed (~506 lines, 62 tests)
└── test_browser.py      # Integration tests — requires Chromium (~954 lines, 99 tests)

pyproject.toml           # Dependencies, optional extras, pytest config, build
README.md
examples/
├── quickstart.py            # Basic usage, Anthropic, OpenAI, state modes
├── stealth_and_proxy.py     # Stealth mode + proxy configuration
├── captcha_and_hitl.py      # CAPTCHA solver hook + Human-in-the-Loop
├── session_and_multitab.py  # Session persistence + multi-tab browsing
└── frameworks.py            # Google Gemini, AWS Bedrock, LangChain, LlamaIndex
```

---

## Running Tests

```bash
# All tests
.venv/bin/python -m pytest tests/ -v

# Unit only (fast, no browser)
.venv/bin/python -m pytest tests/test_unit.py -v

# Browser only
.venv/bin/python -m pytest tests/test_browser.py -v
```

**Playwright Chromium binary** lives at `~/Library/Caches/ms-playwright/`.
If missing: `.venv/bin/python -m playwright install chromium`

### pytest config (pyproject.toml)
- `asyncio_mode = "auto"` — all async tests auto-collected
- `asyncio_default_fixture_loop_scope = "session"` — shared event loop
- `test_browser.py` uses `pytestmark = pytest.mark.asyncio(loop_scope="session")` — required to share session fixture

---

## Public API (`__init__.py` exports)

| Symbol | Type | Notes |
|--------|------|-------|
| `LLMBrowser` | class | Main browser controller |
| `BrowserType` | enum | chrome, chromium, firefox, safari, msedge, webkit |
| `BrowserToolkit` | class | Wraps browser as framework tools |
| `BrowserAction` | Pydantic model | Action validation |
| `PageState` | Pydantic model | Snapshot of page state |
| `CaptchaContext` | dataclass | Passed to CAPTCHA callback |
| `HumanHelpContext` | dataclass | Passed to HITL callback |
| `VIEWPORTS` | dict | Preset viewport configs (mobile, tablet, desktop, etc.) |
| `ViewportResult` | dataclass | Result of a single viewport test run |
| `run_at_viewports` | async function | Run a test callback across multiple viewports |
| `BrowserToolError` | exception | Base class |
| `NavigationError` | exception | Navigation failed |
| `ActionExecutionError` | exception | Action can't execute |
| `ElementNotFoundError` | exception | Target element missing |
| `PageNotReadyError` | exception | Page not usable |

---

## LLMBrowser Constructor Parameters

```python
LLMBrowser(
    headless: bool = True,
    browser: BrowserType = BrowserType.CHROMIUM,
    viewport: dict = {"width": 1280, "height": 800},
    timeout: int = 30_000,          # ms
    user_agent: str = None,
    slow_mo: int = 0,               # ms delay per action
    session_file: str = None,       # path to JSON session state
    max_retries: int = 2,
    dismiss_dialogs: bool = True,   # auto-dismiss cookie banners on navigate
    download_dir: str = None,       # default: ~/.llmbrowser/downloads
    stealth: bool = False,
    proxy: Union[str, dict] = None,        # "http://user:pass@host:port" or Playwright dict
    on_captcha_detected: Callable = None,  # async fn(CaptchaContext) -> str
    remote_cdp_url: str = None,            # wss://... connect instead of launching locally
)
```

Used as async context manager: `async with LLMBrowser(...) as b:`

---

## BrowserToolkit

```python
BrowserToolkit(
    browser: LLMBrowser,
    state_mode: Literal["dom", "aria", "both"] = "dom",
    on_human_needed: Callable = None,  # async fn(HumanHelpContext) -> str
    totp_secret: Optional[str] = None, # TOTP secret key for 2FA/MFA — enables get_totp_code tool
    tools: Optional[list[str]] = None, # allowlist of tool names; None = all tools
)
```

### Adapter methods (all instance methods)

| Method | Returns | Format |
|--------|---------|--------|
| `as_anthropic_tools()` | `list[dict]` | `{name, description, input_schema}` |
| `as_openai_tools()` | `list[dict]` | `{type:"function", function:{name, description, parameters}}` |
| `as_google_tools()` | `list[dict]` | `{function_declarations: [...]}` (single-item list) |
| `as_bedrock_tools()` | `list[dict]` | `{toolSpec: {name, description, inputSchema}}` |
| `as_langchain_tools()` | `list[StructuredTool]` | Requires `llmbrowser[langchain]` |
| `as_llamaindex_tools()` | `list[FunctionTool]` | Requires `llmbrowser[llamaindex]` |
| `get_tools()` | `list[async_fn]` | Plain async callables |

Tool count: **30 base** + optional **solve_captcha** (when `on_captcha_detected` set on `LLMBrowser`) + **get_totp_code** (when `totp_secret` set on `BrowserToolkit`) + **request_human_help** (when `on_human_needed` set).

`_all_schemas()` instance method returns the active schema list depending on callbacks.

### Tool execution
```python
result: str = await toolkit.execute("navigate", url="https://example.com")
```

---

## The 30 Base Tools

Listed in `BrowserToolkit.BASE_TOOLS` (frozenset). Schemas are co-located with each closure inside `_build_tools()` via the `_spec()` helper.

**Navigation**
1. `navigate` — go to URL
2. `go_back` — browser history back
3. `go_forward` — browser history forward
4. `reload_page` — reload the current page

**Observation**
5. `get_page_state` — snapshot without action
6. `switch_frame` — list all frames or switch to a frame by index/URL pattern

**Interaction**
7. `click_element` — click by numeric ID
8. `type_text` — type into input/textarea (clears first)
9. `press_key` — keyboard input ("Enter", "Tab", "Control+a", etc.)
10. `scroll_page` — scroll up/down/left/right (default 300px down)
11. `hover_element` — hover by numeric ID (reveals tooltips/dropdowns)
12. `select_option` — select `<select>` option by value, label, or index
13. `drag_and_drop` — drag source element onto target element by numeric ID
14. `handle_dialog` — pre-register one-shot handler for the next native dialog (accept/dismiss)
15. `get_element_attribute` — read any HTML attribute from an element by numeric ID
16. `wait_for_page` — wait N ms (500–10000, default 2000)

**Storage & Cookies**
17. `manage_storage` — localStorage/sessionStorage get/set/remove/clear/getall
18. `manage_cookies` — list/set/delete cookies for the current context

**Tabs**
19. `new_tab` — open tab, optionally navigate
20. `switch_tab` — switch by index
21. `list_tabs` — list all tabs with metadata
22. `close_tab` — close active tab (raises if last)

**Files & Scripting**
23. `upload_file` — file upload to file-input element
24. `get_downloads` — list downloaded file paths
25. `read_file` — read text/binary files (binary returns summary; >50KB truncated)
26. `execute_script` — run arbitrary JS and return result as string
27. `dismiss_dialogs` — dismiss cookie banners & consent dialogs

**Visual, Location & Export**
28. `take_element_screenshot` — screenshot a single element, returns base64 PNG
29. `set_geolocation` — set browser GPS coordinates and grant geolocation permission
30. `export_pdf` — export current page as PDF, returns saved file path

---

## Key JavaScript in core.py

### `_CHROMIUM_ARGS`
```python
["--disable-blink-features=AutomationControlled", "--no-sandbox"]
```
**Important:** This always suppresses `navigator.webdriver`, even without stealth mode. Do not write tests asserting `navigator.webdriver === true` without stealth.

### `_STEALTH_JS`
Injected via `context.add_init_script()` before page JS. Patches:
- `navigator.webdriver` → `undefined`
- `window.chrome` → fake Chrome API
- `navigator.languages` → `["en-US", "en"]`
- `navigator.plugins` / `navigator.mimeTypes` → fake PDF viewer
- `navigator.permissions.query` → fake notification result
- WebGL `getParameter(UNMASKED_VENDOR_WEBGL)` → `"Intel Inc."`
- WebGL `getParameter(UNMASKED_RENDERER_WEBGL)` → `"Intel Iris OpenGL Engine"`
- Canvas `toDataURL()` → imperceptible LSB noise per run

### `_COOKIE_DISMISS_JS`
Two-stage: (1) known library selectors (OneTrust, Cookiebot, Didomi, TrustArc), (2) text-pattern matching on visible containers.

### `_CAPTCHA_DETECT_JS`
Detects: `.h-captcha[data-sitekey]`, `.g-recaptcha[data-sitekey]`, reCAPTCHA v3 (script src `?render=`), reCAPTCHA v2 iframe fallback. Returns `{type, sitekey}` or `null`.

### `_CAPTCHA_INJECT_JS`
Receives `[token, type]` (destructured array — Playwright `evaluate` takes single arg).
Injects into textarea + fires registered callback for reCAPTCHA and hCaptcha.

### ARIA extraction (`_extract_aria_tree`)
**Uses JS DOM walk** (NOT `page.accessibility` — removed in Playwright ≥1.46).
Walks DOM, emits only semantic/role-bearing elements (buttons, inputs, headings, nav, etc.).
`_aria_to_text()` helper (still present in code) is legacy — not currently called.

---

## JavaScript in Other Files

### `injector.py` — ANNOTATION_JS
- Assigns `data-llm-id` (1, 2, 3…) to interactive elements
- Draws red border boxes (fixed-position, `#FF3B30`) with white label
- Covers: light DOM, shadow DOM, same-origin iframes
- Skips invisible elements (zero width/height)

### `injector.py` — CLEANUP_JS
Removes all `.llm-annotation-box` divs and `data-llm-id` attributes.

### `dom_extractor.py` — DOM_EXTRACTION_JS
Tree walk to depth 12. Skips `script/style/svg/meta`. Prunes invisible non-interactive elements.

### `dom_extractor.py` — INTERACTIVE_ELEMENTS_JS
Collects `[data-llm-id]` elements from main doc + iframes. Returns list with id, tag, text, rect, in_viewport.

### `utils.py` — `_SETTLE_JS`
MutationObserver that resolves after 300ms of no DOM mutations (debounced). More reliable than network-idle for SPAs.

---

## Design Patterns

### Element annotation lifecycle
1. `ANNOTATION_JS` injects IDs + draws boxes
2. Screenshot taken + DOM/ARIA extracted
3. `CLEANUP_JS` removes all annotations
4. Agent references elements by numeric ID

### Action retry
Click/type retry up to `max_retries` (default 2):
- `ElementNotFoundError` → re-annotate immediately
- `ActionExecutionError` (timeout) → exponential backoff (800ms × attempt) + re-annotate

### Proxy
`_parse_proxy()` normalizes URL strings → Playwright dict. Applied at context level (not launch), so it scopes per-context and survives `load_session()`. Skipped silently when `remote_cdp_url` is set (warning logged at `__init__` time).

### Remote CDP
When `remote_cdp_url` is set, `_start()` calls `playwright.chromium.connect_over_cdp(url)` instead of `_launch_browser()`. The `browser=`, `headless=`, `slow_mo=`, and `proxy=` params are ignored. Stealth, session_file, viewport, user_agent, and all tools work normally. `load_session()` also respects `_remote_cdp_url` for the proxy skip logic.

### Connecting to an existing browser — `find_browser()` and `attach()`

**`find_browser(port=9222)`** — async classmethod. Hits `http://localhost:{port}/json/version` and returns the `webSocketDebuggerUrl`. Raises `RuntimeError` with a helpful message if nothing is listening on that port.

**`attach(port, binary, profile_dir, headless, **kwargs)`** — async classmethod. Locates the system Chrome/Edge binary (`find_chrome_binary()` in `utils.py`), spawns it as a subprocess with `--remote-debugging-port`, waits up to 15s for CDP to be ready via `_wait_for_cdp()`, then returns a fully initialised `LLMBrowser` instance. The subprocess is stored in `_subprocess` and is terminated automatically in `close()`.

`find_chrome_binary()` in `utils.py` checks OS-specific hard-coded paths first (macOS `.app` bundles, Windows `%PROGRAMFILES%`), then falls back to `shutil.which()` for Linux PATH lookup.

**`_subprocess`** is initialised to `None` in `__init__` and set by `attach()`. `close()` calls `proc.terminate()` if it is not `None`.

### Stealth
`context.add_init_script(_STEALTH_JS)` — called in both `_start()` and `load_session()` context creation.

### CAPTCHA flow
1. `detect_captcha()` → calls `_CAPTCHA_DETECT_JS`
2. Calls `on_captcha_detected(CaptchaContext)` → returns token string
3. `inject_captcha_token(token, type)` → calls `_CAPTCHA_INJECT_JS`
4. Auto-triggered in `navigate()` if callback set

### HITL flow
`request_human_help` tool captures screenshot + page state + URL, wraps in `HumanHelpContext`, calls `on_human_needed`, returns human's response string to agent.

### Session persistence
Playwright `storage_state` (JSON with cookies + localStorage). `session_file=` on init auto-loads.

### New element-targeting methods (v0.2.0)
All follow the same pattern: `_inject_annotations()` → `_find_element(id)` → Playwright locator call.
- `hover_element(id)` → `locator.hover()`
- `select_option(id, value/label/index)` → `locator.select_option(**kwargs)`
- `get_element_attribute(id, attr)` → `locator.get_attribute(attr)`
- `drag_and_drop(src_id, tgt_id)` → `src_locator.drag_to(tgt_locator)`
- `take_element_screenshot(id)` → `locator.screenshot()` → base64 PNG string

### manage_storage
Uses destructured JS args pattern to pass multiple values to `page.evaluate`:
```python
page.evaluate("([s, k, v]) => window[s].setItem(k, v)", [js_store, key, value])
```
`localStorage`/`sessionStorage` only available on `http://` or `https://` origins — raises `SecurityError` on `about:blank`.

### manage_cookies
`BrowserContext.add_cookies` requires EITHER `url` OR `domain`+`path` — not both.
When page URL is `http`/`https`, passes only `url`. Otherwise falls back to `domain="localhost"` + `path`.

### handle_next_dialog
Synchronous method (not async). Registers a one-shot `page.once("dialog", handler)` listener before the action that triggers the dialog. The agent tool `handle_dialog` calls this then instructs the agent to trigger the dialog.

### export_pdf
Uses `page.pdf(path=path)`. Only works in Chromium (not Firefox/WebKit). Defaults to `_download_dir`.

### set_geolocation
Calls `context.set_geolocation({"latitude": lat, "longitude": lon, "accuracy": acc})` then `context.grant_permissions(["geolocation"])`. Affects all pages in the current context.

---

## Schemas

### BrowserAction (Pydantic, cross-field validated via `model_validator(mode="after")`)
- `click`/`type` → require `target_id`
- `type` → requires non-empty `value`
- `navigate` → requires `value` (URL)
- `key` → requires `value` (key name)
- `scroll_amount` range: [1, 5000], default 300

**Note:** Must use `model_validator(mode="after")` not `field_validator` — Pydantic v2 `field_validator` does NOT run when a field uses its default value.

### PageState (Pydantic)
```python
url, title, screenshot_b64, simplified_dom, aria_tree, interactive_elements, scroll_position, page_height
```
`simplified_dom` is empty string (not None) when mode="aria".
`aria_tree` is None when mode="dom".

---

## Dependencies

### Core
- `playwright>=1.40.0`
- `pydantic>=2.0.0`
- `py-wraps-data-types>=0.1.1` (PyPI) — provides `Variant` for BrowserType enum

### Optional extras (pip install llmbrowser[...])
- `anthropic` — `anthropic>=0.40.0`
- `openai` — `openai>=1.0.0`
- `google` — `google-generativeai>=0.8.0`
- `bedrock` — `anthropic[bedrock]>=0.40.0`
- `langchain` — `langchain-core>=0.1.0`
- `llamaindex` — `llama-index-core>=0.10.0`
- `totp` — `pyotp>=2.9.0` (enables `get_totp_code` 2FA tool)
- `all` — everything above

### Dev
- `pytest>=8.0.0`, `pytest-asyncio>=0.23.0`

---

## Test Patterns

### Session-scoped browser fixture
One Chromium instance shared across all integration tests. Defined in `conftest.py` with `scope="session"`. Created with `dismiss_dialogs=False, stealth=False` so individual tests can override.

### `load_html(browser, html)`
Uses `page.set_content()` — no network. Retries 3× with 0.5s delay to survive chrome-error page bleed from `test_navigation_error_on_unreachable`.

### HTML templates in conftest
- `BASIC_HTML` — button, input, link
- `SCROLLABLE_HTML` — 3000px tall, top/bottom elements
- `RECAPTCHA_V2_HTML` — reCAPTCHA v2 with callback
- `HCAPTCHA_HTML` — hCaptcha with callback
- `SELECT_HTML` — `<select>` with multiple options (for `select_option` tests)
- `DIALOG_HTML` — button that triggers `window.confirm()` (for `handle_dialog` tests)
- `IFRAME_HTML` — page with an embedded `<iframe>` (for `switch_frame` tests)
- `DRAG_HTML` — two `<button>` elements side by side (for `drag_and_drop` tests)

### Test classes added in v0.2.0 (test_browser.py)
- `TestGoForwardReload` — uses two real `navigate()` calls (NOT `set_content`) to build clean history
- `TestHoverAndSelect` — hover fires mouseover event; select by value/label/index
- `TestManageStorage` — first test navigates to `https://example.com` before `load_html` to ensure http origin
- `TestManageCookies` — all tests navigate to `https://example.com` first (cookies require valid http URL)
- `TestExecuteScript` — evaluates JS expressions and returns string results
- `TestGetElementAttribute` — reads href, placeholder, data-* attributes
- `TestHandleDialog` — clicks button by `#btn` HTML id (NOT `[data-llm-id='...']`) after `get_state()` cleanup
- `TestDragAndDrop` — uses `<button>` elements so ANNOTATION_JS annotates them
- `TestTakeElementScreenshot` — verifies base64 PNG returned
- `TestSwitchFrame` — verifies frame listing and switching by index
- `TestSetGeolocation` — verifies `navigator.geolocation` returns set coordinates
- `TestExportPdf` — verifies PDF file created at expected path

### Test ordering in TestNavigation
`test_go_back` must come **before** `test_navigation_error_on_unreachable`.
Reason: the unreachable test leaves browser in `chrome-error://` state which blocks subsequent navigations.

### File tests (`TestFileHandling`)
All methods are `async def` (not `def`) to match module-level `pytestmark = pytest.mark.asyncio(loop_scope="session")`.

---

## Known Gotchas

1. **`navigator.webdriver`** — always `False`/`undefined` in Chromium builds because `--disable-blink-features=AutomationControlled` is in `_CHROMIUM_ARGS`. Never assert it's `True` without stealth.

2. **`page.accessibility` removed** in Playwright ≥1.46. Use the JS-based `_extract_aria_tree()` method instead.

3. **Pydantic v2 `field_validator`** does not run when a field uses its default value. Use `model_validator(mode="after")` for cross-field validation.

4. **pytest-asyncio 1.x** requires `asyncio_default_fixture_loop_scope = "session"` in pyproject.toml AND `pytestmark = pytest.mark.asyncio(loop_scope="session")` in the test module for session-scoped async fixtures to work without deadlocking.

5. **`page.evaluate` takes a single argument.** To pass multiple values, use a list and destructure in JS: `page.evaluate("([a, b]) => ...", [a, b])`.

6. **chrome-error page blocks navigation.** After a NavigationError from an unreachable URL, the chrome-error page may block subsequent `goto`/`set_content` calls. The `load_html` helper retries to handle this.

7. **LangChain/LlamaIndex adapters use lazy imports.** They raise `ImportError` with a helpful install message if the optional dependency is missing.

8. **ANNOTATION_JS only annotates semantic interactive elements** — `<button>`, `<input>`, `<a>`, `<select>`, `<textarea>`, and elements with `role` attributes. Plain `<div>` or `<span>` elements are NOT annotated, even with `tabindex="0"`. Tests for drag-and-drop and similar tools must use `<button>` elements.

9. **`get_state()` / CLEANUP_JS removes `data-llm-id` attributes.** After calling `get_state()` (or any method that calls it internally), all annotation IDs are gone. Do not rely on `[data-llm-id='N']` selectors in tests after a `get_state()` call — click by HTML `id` attribute instead.

10. **`localStorage`/`sessionStorage` require a real http/https origin.** They raise `SecurityError` on `about:blank`, `chrome-error://`, and other non-http pages. Navigate to a real URL before calling `manage_storage`.

11. **`BrowserContext.add_cookies` requires `url` OR `domain+path` — never both.** Passing both raises `Cookie should have either url or path`. The `manage_cookies` implementation handles this automatically, but cookie tests must navigate to a real http URL first.

12. **`chrome-error://` in history affects `go_back()`.** After `test_navigation_error_on_unreachable`, the chrome-error URL is in the browser history. Tests using `go_back()` after `set_content` may navigate into that error URL. Use real `navigate()` calls to build clean history instead of `set_content`.

13. **`StopIteration` inside async coroutines becomes `RuntimeError` (PEP 479).** `next(generator)` on an empty generator inside `async def` raises `RuntimeError: coroutine raised StopIteration`. Always check that `interactive_elements` is non-empty before using `next()`.

---

## Build & Release

```bash
# Install in dev mode
uv sync

# Install with all optional deps
uv sync --extra all

# Build sdist + wheel locally
uv build
```

Build backend: Hatchling. Wheel packages only the `llmbrowser/` directory.

### CI/CD — GitHub Actions

Two workflows in `.github/workflows/`:

| File | Trigger | Jobs |
|------|---------|------|
| `test.yml` | push/PR to `master` | unit tests (3.11, 3.12, 3.13) + browser tests (3.13) |
| `publish.yml` | `git push origin v*` | same tests → build → PyPI publish |

`test.yml` is what the README badge points to.

### Releasing to PyPI — step-by-step checklist

**One-time setup (do once before first release):**

1. **Register the Trusted Publisher on PyPI** (no API token needed):
   - Go to https://pypi.org/manage/account/publishing/
   - Project name: `llmbrowser`
   - GitHub owner: `Ponprabhakar-pg`
   - Repository name: `llmbrowser`
   - Workflow filename: `publish.yml`
   - Environment name: `pypi`

3. **Create the `pypi` environment on GitHub:**
   - Repo → Settings → Environments → New environment → name it `pypi`
   - Optionally add a protection rule (require manual approval before publish)

**Each release:**

```bash
# 1. Bump version in pyproject.toml and llmbrowser/__init__.py
# 2. Commit
git commit -am "chore: bump version to v0.x.y"
# 3. Tag
git tag v0.x.y
# 4. Push both
git push origin master
git push origin v0.x.y
# The publish.yml workflow fires automatically.
```

### TestPyPI (optional dry run before first real release)
Uncomment the `repository-url` block in `publish.yml` and point it at `https://test.pypi.org/legacy/`.
Register the same Trusted Publisher at https://test.pypi.org/manage/account/publishing/ first.
