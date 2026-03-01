# CLAUDE.md — llmbrowser

Reference file for Claude Code. Update this file whenever the codebase changes.

---

## Project Overview

**Package:** `llmbrowser` v0.1.0
**Purpose:** Framework-agnostic browser tool for AI agents (Playwright-based)
**Python:** ≥3.11
**Repo:** `https://github.com/Ponprabhakar-pg/llmbrowser`

---

## Directory Structure

```
llmbrowser/
├── __init__.py          # Public API exports
├── core.py              # LLMBrowser (~1389 lines) — main browser class
├── browser_tools.py     # BrowserToolkit (~768 lines) — framework adapters
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
├── test_unit.py         # Unit tests — no browser needed (~478 lines, 58 tests)
└── test_browser.py      # Integration tests — requires Chromium (~476 lines, 51 tests)

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

Tool count: **16 base** + optional **solve_captcha** (when `on_captcha_detected` set on `LLMBrowser`) + **get_totp_code** (when `totp_secret` set on `BrowserToolkit`) + **request_human_help** (when `on_human_needed` set).

`_all_schemas()` instance method returns the active schema list depending on callbacks.

### Tool execution
```python
result: str = await toolkit.execute("navigate", url="https://example.com")
```

---

## The 16 Base Tools

Listed in `BrowserToolkit.BASE_TOOLS` (frozenset). Schemas are co-located with each closure inside `_build_tools()` via the `_spec()` helper.

1. `navigate` — go to URL
2. `get_page_state` — snapshot without action
3. `click_element` — click by numeric ID
4. `type_text` — type into input/textarea (clears first)
5. `press_key` — keyboard input ("Enter", "Tab", "Control+a", etc.)
6. `scroll_page` — scroll up/down/left/right (default 300px down)
7. `go_back` — browser history back
8. `wait_for_page` — wait N ms (500–10000, default 2000)
9. `new_tab` — open tab, optionally navigate
10. `switch_tab` — switch by index
11. `list_tabs` — list all tabs with metadata
12. `close_tab` — close active tab (raises if last)
13. `upload_file` — file upload to file-input element
14. `get_downloads` — list downloaded file paths
15. `read_file` — read text/binary files (binary returns summary; >50KB truncated)
16. `dismiss_dialogs` — dismiss cookie banners & consent dialogs

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
| `test.yml` | push/PR to `main` | unit tests (3.11, 3.12, 3.13) + browser tests (3.13) |
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
git push origin main
git push origin v0.x.y
# The publish.yml workflow fires automatically.
```

### TestPyPI (optional dry run before first real release)
Uncomment the `repository-url` block in `publish.yml` and point it at `https://test.pypi.org/legacy/`.
Register the same Trusted Publisher at https://test.pypi.org/manage/account/publishing/ first.
