# llmbrowser

**A framework-agnostic browser tool for AI agents.**

[![PyPI version](https://img.shields.io/pypi/v/llmbrowser.svg)](https://pypi.org/project/llmbrowser/)
[![Python](https://img.shields.io/pypi/pyversions/llmbrowser.svg)](https://pypi.org/project/llmbrowser/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Tests](https://github.com/Ponprabhakar-pg/llmbrowser/actions/workflows/test.yml/badge.svg)](https://github.com/Ponprabhakar-pg/llmbrowser/actions)

Give your AI agent a real browser. llmbrowser wraps [Playwright](https://playwright.dev/python/) into **30 ready-to-use browser tools** and converts them to whichever tool format your LLM framework expects — all from the same codebase.

```python
from llmbrowser import LLMBrowser, BrowserToolkit

async with LLMBrowser() as browser:
    toolkit = BrowserToolkit(browser)
    tools   = toolkit.as_anthropic_tools()   # or as_openai_tools(), as_google_tools(), …
```

---

## Table of Contents

- [Features](#features)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Framework Integrations](#framework-integrations)
  - [Anthropic (Claude)](#anthropic-claude)
  - [OpenAI & Compatible APIs](#openai--compatible-apis)
  - [Google Gemini](#google-gemini)
  - [AWS Bedrock](#aws-bedrock)
  - [LangChain](#langchain)
  - [LlamaIndex](#llamaindex)
- [The 30 Browser Tools](#the-30-browser-tools)
- [Page State Modes](#page-state-modes)
- [Advanced Features](#advanced-features)
  - [Stealth Mode](#stealth-mode)
  - [Proxy Support](#proxy-support)
  - [CAPTCHA Solver Hook](#captcha-solver-hook)
  - [Human-in-the-Loop (HITL)](#human-in-the-loop-hitl)
  - [Session Persistence](#session-persistence)
  - [Multi-Tab Browsing](#multi-tab-browsing)
- [API Reference](#api-reference)
- [Contributing](#contributing)
- [License](#license)

---

## Features

- **Framework-agnostic** — one package, six adapters: Anthropic, OpenAI, Google Gemini, AWS Bedrock, LangChain, LlamaIndex
- **30 built-in browser tools** — navigate, click, type, scroll, hover, select dropdowns, storage, cookies, script execution, multi-tab, file upload, and more
- **Three page-state modes** — full DOM tree, token-efficient ARIA tree, or both
- **Stealth mode** — patches `navigator.webdriver`, WebGL, canvas, plugins and more to avoid bot detection
- **Proxy support** — HTTP/HTTPS and SOCKS5 proxies with optional credentials
- **CAPTCHA solver hook** — async callback that integrates with any solver API (CapSolver, 2captcha, etc.)
- **Human-in-the-Loop (HITL)** — agent calls `request_human_help` when it needs a human; your callback decides what happens
- **Session persistence** — save and restore cookies + localStorage across runs
- **Multi-tab** — open, switch, and close tabs programmatically
- **Remote browser (CDP)** — connect to Browserbase, Anchor, or any self-hosted Chromium instead of launching locally
- **Retry & recovery** — automatic retry with backoff on element-not-found and action timeouts

---

## Installation

```bash
pip install llmbrowser
```

Install with your framework's extra to get the optional SDK dependency:

```bash
pip install llmbrowser[anthropic]    # Anthropic Claude
pip install llmbrowser[openai]       # OpenAI, DeepSeek, Azure OpenAI, Groq, …
pip install llmbrowser[google]       # Google Gemini (AI Studio & Vertex AI)
pip install llmbrowser[bedrock]      # AWS Bedrock
pip install llmbrowser[langchain]    # LangChain
pip install llmbrowser[llamaindex]   # LlamaIndex
pip install llmbrowser[all]          # everything
```

> **Playwright browsers** are downloaded automatically on first use. To install manually:
> ```bash
> python -m playwright install chromium
> ```

---

## Quick Start

```python
import asyncio
from llmbrowser import LLMBrowser, BrowserToolkit

async def main():
    async with LLMBrowser(headless=True) as browser:
        toolkit = BrowserToolkit(browser, state_mode="aria")  # token-efficient

        # Execute any tool by name and get back a formatted text result
        result = await toolkit.execute("navigate", url="https://example.com")
        print(result)

asyncio.run(main())
```

---

## Framework Integrations

All adapters expose the same tools — only the schema format changes.

### Anthropic (Claude)

```python
import anthropic
from llmbrowser import LLMBrowser, BrowserToolkit

client = anthropic.Anthropic()

async with LLMBrowser(headless=True) as browser:
    toolkit = BrowserToolkit(browser, state_mode="aria")
    tools   = toolkit.as_anthropic_tools()   # [{name, description, input_schema}]

    messages = [{"role": "user", "content": "Go to example.com and summarise the page."}]

    while True:
        response = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=4096,
            tools=tools, messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            print(next(b.text for b in response.content if hasattr(b, "text")))
            break

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                result = await toolkit.execute(block.name, **block.input)
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})
        messages.append({"role": "user", "content": tool_results})
```

### OpenAI & Compatible APIs

```python
import json, openai
from llmbrowser import LLMBrowser, BrowserToolkit

client = openai.AsyncOpenAI()   # swap for AzureOpenAI, Groq, Mistral, etc.

async with LLMBrowser(headless=True) as browser:
    toolkit = BrowserToolkit(browser, state_mode="aria")
    tools   = toolkit.as_openai_tools()   # [{type: "function", function: {...}}]

    messages = [
        {"role": "system",  "content": "You are a web browsing agent."},
        {"role": "user",    "content": "Go to example.com and summarise the page."},
    ]

    while True:
        response = await client.chat.completions.create(model="gpt-4o", tools=tools, messages=messages)
        msg = response.choices[0].message
        messages.append(msg)

        if response.choices[0].finish_reason == "stop":
            print(msg.content)
            break

        tool_results = []
        for tc in msg.tool_calls:
            result = await toolkit.execute(tc.function.name, **json.loads(tc.function.arguments))
            tool_results.append({"role": "tool", "tool_call_id": tc.id, "content": result})
        messages.extend(tool_results)
```

The OpenAI adapter also works with **DeepSeek, GitHub Copilot, Azure OpenAI, Groq, Mistral, Together AI, Ollama, and LiteLLM** — just swap the client.

### Google Gemini

```python
import google.generativeai as genai
import google.generativeai.types as genai_types
from llmbrowser import LLMBrowser, BrowserToolkit

async with LLMBrowser(headless=True) as browser:
    toolkit = BrowserToolkit(browser, state_mode="aria")
    tools   = toolkit.as_google_tools()   # [{function_declarations: [...]}]

    model = genai.GenerativeModel("gemini-1.5-pro", tools=tools)
    chat  = model.start_chat(enable_automatic_function_calling=False)
    response = chat.send_message("Go to example.com and summarise the page.")

    while True:
        calls = [p.function_call for c in response.candidates for p in c.content.parts if p.function_call.name]
        if not calls:
            print(next(p.text for c in response.candidates for p in c.content.parts if p.text))
            break

        responses = []
        for call in calls:
            result = await toolkit.execute(call.name, **dict(call.args))
            responses.append(genai_types.FunctionResponse(name=call.name, response={"result": result}))
        response = chat.send_message([genai_types.Part(function_response=r) for r in responses])
```

### AWS Bedrock

```python
import boto3
from llmbrowser import LLMBrowser, BrowserToolkit

bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")

async with LLMBrowser(headless=True) as browser:
    toolkit = BrowserToolkit(browser, state_mode="aria")
    tools   = toolkit.as_bedrock_tools()   # [{toolSpec: {name, description, inputSchema}}]

    messages = [{"role": "user", "content": [{"text": "Go to example.com and summarise the page."}]}]

    while True:
        response    = bedrock.converse(modelId="anthropic.claude-sonnet-4-6-20250514-v1:0",
                                       messages=messages, toolConfig={"tools": tools})
        output_msg  = response["output"]["message"]
        stop_reason = response["stopReason"]
        messages.append(output_msg)

        if stop_reason == "end_turn":
            print(next(b["text"] for b in output_msg["content"] if "text" in b))
            break

        tool_results = []
        for block in output_msg["content"]:
            tu = block.get("toolUse", block)
            result = await toolkit.execute(tu["name"], **tu["input"])
            tool_results.append({"toolResult": {"toolUseId": tu["toolUseId"], "content": [{"text": result}]}})
        messages.append({"role": "user", "content": tool_results})
```

### LangChain

```python
from langchain_anthropic import ChatAnthropic
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate
from llmbrowser import LLMBrowser, BrowserToolkit

async with LLMBrowser(headless=True) as browser:
    toolkit = BrowserToolkit(browser, state_mode="aria")
    tools   = toolkit.as_langchain_tools()   # list[StructuredTool]

    llm    = ChatAnthropic(model="claude-sonnet-4-6")
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a web browsing agent."),
        ("human", "{input}"),
        ("placeholder", "{agent_scratchpad}"),
    ])
    agent    = create_tool_calling_agent(llm, tools, prompt)
    executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

    result = await executor.ainvoke({"input": "Go to example.com and summarise the page."})
    print(result["output"])
```

### LlamaIndex

```python
from llama_index.core.agent import ReActAgent
from llama_index.llms.anthropic import Anthropic
from llmbrowser import LLMBrowser, BrowserToolkit

async with LLMBrowser(headless=True) as browser:
    toolkit = BrowserToolkit(browser, state_mode="aria")
    tools   = toolkit.as_llamaindex_tools()   # list[FunctionTool]

    agent   = ReActAgent.from_tools(tools, llm=Anthropic(model="claude-sonnet-4-6"), verbose=True)
    result  = await agent.achat("Go to example.com and summarise the page.")
    print(result)
```

---

## The 30 Browser Tools

Every adapter exposes the same 30 tools. Up to three more are added automatically when you register optional callbacks (see [Advanced Features](#advanced-features)).

### Navigation & page control

| Tool | Description |
|------|-------------|
| `navigate` | Go to a URL and wait for the page to settle |
| `go_back` | Navigate back in browser history |
| `go_forward` | Navigate forward in browser history |
| `reload_page` | Reload the current page |
| `wait_for_page` | Wait N milliseconds for dynamic content to load (500–10 000) |

### Observation

| Tool | Description |
|------|-------------|
| `get_page_state` | Capture URL, title, screenshot, elements, and DOM/ARIA tree |
| `get_element_attribute` | Read any HTML attribute (`href`, `src`, `data-*`, `aria-*`) from an element |

### Interaction

| Tool | Description |
|------|-------------|
| `click_element` | Click an element by its numeric ID |
| `type_text` | Type into an input or textarea (clears existing content first) |
| `press_key` | Press a keyboard key — `"Enter"`, `"Tab"`, `"Control+a"`, etc. |
| `hover_element` | Hover over an element to reveal tooltips or dropdown menus |
| `select_option` | Select a `<select>` dropdown option by value, label, or index |
| `scroll_page` | Scroll up / down / left / right by N pixels (default 300) |
| `handle_dialog` | Pre-register a handler for the next `alert` / `confirm` / `prompt` dialog |

### Storage & cookies

| Tool | Description |
|------|-------------|
| `manage_storage` | Read / write / clear `localStorage` or `sessionStorage` |
| `manage_cookies` | List, set, or delete cookies for the current browser context |

### Tabs

| Tool | Description |
|------|-------------|
| `new_tab` | Open a new browser tab, optionally navigate to a URL |
| `switch_tab` | Switch to a tab by its index |
| `list_tabs` | List all open tabs with URL, title, and active status |
| `close_tab` | Close the currently active tab |

### Files & scripting

| Tool | Description |
|------|-------------|
| `upload_file` | Set a file on a `<input type="file">` element |
| `get_downloads` | List files downloaded in the current session |
| `read_file` | Read a local file's contents (text or binary summary) |
| `execute_script` | Run arbitrary JavaScript on the page and return the result |
| `dismiss_dialogs` | Dismiss cookie consent banners and GDPR dialogs |
| `export_pdf` | Export the current page as a PDF file (Chromium headless only) |

### Visual & interaction

| Tool | Description |
|------|-------------|
| `drag_and_drop` | Drag one element onto another (Kanban, sortable lists, file-drop zones) |
| `take_element_screenshot` | Screenshot a single element and return it as a base64-encoded PNG |

### Frames & location

| Tool | Description |
|------|-------------|
| `switch_frame` | List all iframes on the page and their URLs |
| `set_geolocation` | Override the browser's reported GPS location |

### Optional tools (added automatically)

| Tool | Added when |
|------|-----------|
| `solve_captcha` | `on_captcha_detected=` callback provided to `LLMBrowser` |
| `get_totp_code` | `totp_secret=` provided to `BrowserToolkit` (requires `llmbrowser[totp]`) |
| `request_human_help` | `on_human_needed=` callback provided to `BrowserToolkit` |

---

## Page State Modes

Each tool call that returns page state includes a screenshot, interactive-element list, scroll position, and a text representation of the page structure. Three modes control that representation:

| Mode | Token cost | Best for |
|------|-----------|---------|
| `"dom"` | High | Debugging, complex layouts, full structure |
| `"aria"` | ~60% lower | Production agents, cost-sensitive workloads |
| `"both"` | Highest | Comparison, evaluation |

```python
toolkit = BrowserToolkit(browser, state_mode="aria")   # recommended for agents
```

Or access the raw `PageState` object directly:

```python
state = await browser.get_state(mode="aria")
print(state.aria_tree)            # compact ARIA text
print(state.screenshot_b64)       # base64 JPEG
print(state.interactive_elements) # [{id, tag, text, rect, in_viewport}]
print(state.scroll_position)      # {x, y}
print(state.page_height)          # total scrollable height in px
```

---

## Advanced Features

### Stealth Mode

Patches common browser-fingerprinting signals so bot-detection systems cannot identify the automated browser.

```python
async with LLMBrowser(headless=True, stealth=True) as browser:
    await browser.navigate("https://example.com")
```

**What gets patched:**

| Signal | Without stealth | With stealth |
|--------|----------------|-------------|
| `navigator.webdriver` | `false` (suppressed by Chromium arg) | `undefined` |
| `window.chrome` | absent in headless | fake Chrome object |
| `navigator.languages` | varies | `["en-US", "en"]` |
| `navigator.plugins` | empty | fake PDF viewer |
| `navigator.permissions` | real result | fake notification result |
| WebGL vendor / renderer | real GPU info | `"Intel Inc."` / `"Intel Iris OpenGL Engine"` |
| Canvas fingerprint | deterministic | per-run noise (LSB flip) |

Combine with a realistic user-agent and a proxy for maximum evasion:

```python
async with LLMBrowser(
    headless=True,
    stealth=True,
    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 ...",
    proxy="http://user:pass@proxy.example.com:8080",
) as browser:
    ...
```

---

### Proxy Support

Pass a URL string or a Playwright proxy dict:

```python
# HTTP proxy with credentials
async with LLMBrowser(proxy="http://user:pass@proxy.example.com:8080") as browser: ...

# SOCKS5
async with LLMBrowser(proxy="socks5://proxy.example.com:1080") as browser: ...

# Playwright dict (for bypass lists and other advanced options)
async with LLMBrowser(proxy={
    "server":   "http://proxy.example.com:8080",
    "username": "user",
    "password": "pass",
    "bypass":   "localhost,127.0.0.1",
}) as browser: ...
```

The proxy is applied at the browser-context level so it covers all pages, tabs, and network requests.

---

### CAPTCHA Solver Hook

Register an async callback on `LLMBrowser`. After every `navigate()`, llmbrowser automatically detects any CAPTCHA on the page and calls your callback to solve it — the agent never needs to handle CAPTCHAs explicitly.

**Supported CAPTCHA types:** `recaptcha_v2`, `recaptcha_v3`, `hcaptcha`

```python
from llmbrowser import LLMBrowser, CaptchaContext

async def my_solver(ctx: CaptchaContext) -> str:
    # ctx.type    — "recaptcha_v2" | "recaptcha_v3" | "hcaptcha"
    # ctx.sitekey — public sitekey required by solver APIs
    # ctx.url     — current page URL

    # Example: CapSolver
    import capsolver
    capsolver.api_key = "YOUR_API_KEY"
    solution = capsolver.solve({
        "type": "ReCaptchaV2Task",
        "websiteURL": ctx.url,
        "websiteKey": ctx.sitekey,
    })
    return solution["gRecaptchaResponse"]

async with LLMBrowser(headless=True, on_captcha_detected=my_solver) as browser:
    await browser.navigate("https://site-with-captcha.example.com")
    # CAPTCHA automatically detected, solved, and token injected ✓
```

You can also call the detection and injection steps manually:

```python
ctx = await browser.detect_captcha()        # returns CaptchaContext or None
if ctx:
    token = await my_solver(ctx)
    ok    = await browser.inject_captcha_token(token, ctx.type)
```

When `on_captcha_detected` is set, a **`solve_captcha`** tool is also added to the agent's toolbox so the agent can trigger solving on demand.

---

### Human-in-the-Loop (HITL)

Register an async callback on `BrowserToolkit`. This adds a `request_human_help` tool to the agent's toolbox. When the agent encounters something it cannot handle (an MFA code, an unexpected page, ambiguous instructions), it calls the tool with a reason. Your callback receives full context — including a screenshot — and returns the human's response.

```python
from llmbrowser import LLMBrowser, BrowserToolkit, HumanHelpContext

async def my_handler(ctx: HumanHelpContext) -> str:
    # ctx.reason          — agent's explanation of why it needs help
    # ctx.url             — current page URL
    # ctx.title           — current page title
    # ctx.screenshot_b64  — base64 JPEG of what the agent currently sees
    # ctx.session_id      — stable ID for this session (useful for webhooks)

    # Simple CLI example — replace with Slack, email, webhook, etc.
    print(f"\n[Agent needs help] {ctx.reason}")
    print(f"  Page: {ctx.title} @ {ctx.url}")
    return input("  Your response: ")

async with LLMBrowser(headless=True) as browser:
    toolkit = BrowserToolkit(browser, on_human_needed=my_handler)
    # 'request_human_help' is now part of all adapter outputs
    tools = toolkit.as_anthropic_tools()   # 31 tools (30 base + request_human_help)
```

**Common patterns:**

```python
# Slack notification + poll for reply
async def slack_handler(ctx: HumanHelpContext) -> str:
    msg_ts = await post_slack_message(ctx.reason, image_b64=ctx.screenshot_b64)
    return await wait_for_slack_reply(thread_ts=msg_ts)

# Webhook — pause the agent and resume when a human responds
async def webhook_handler(ctx: HumanHelpContext) -> str:
    event_id = await emit_pause_event(ctx)
    return await wait_for_resume_event(event_id)   # blocks until POST /resume
```

---

### Session Persistence

Save and restore browser state (cookies + localStorage) so agents can pick up where they left off without logging in again.

```python
# First run — log in and save the session
async with LLMBrowser(headless=True) as browser:
    await browser.navigate("https://example.com/login")
    # … agent completes login …
    await browser.save_session("session.json")

# Subsequent runs — restore the session instantly
async with LLMBrowser(headless=True, session_file="session.json") as browser:
    await browser.navigate("https://example.com/dashboard")   # already logged in ✓
```

You can also load a session after startup:

```python
async with LLMBrowser(headless=True) as browser:
    await browser.load_session("session.json")
    await browser.navigate("https://example.com/dashboard")
```

---

### Multi-Tab Browsing

```python
async with LLMBrowser(headless=True) as browser:
    # Open a second tab
    tab_idx = await browser.new_tab("https://example.com")

    # Switch between tabs by index (0-based)
    await browser.switch_tab(0)          # back to original tab
    await browser.switch_tab(tab_idx)    # back to new tab

    # Inspect all open tabs
    tabs = await browser.list_tabs()
    # [{"index": 0, "url": "...", "title": "...", "active": False},
    #  {"index": 1, "url": "...", "title": "...", "active": True}]

    # Close the active tab (raises if it is the last one)
    await browser.close_tab()

    print(browser.tab_count)    # number of open tabs
    print(browser.active_tab)   # index of the active tab
```

Multi-tab tools are included in all 30 base tools, so agents can manage tabs without any extra configuration.

---

### Connecting to an Existing Browser

#### Auto-detect a running browser

If you've already launched Chrome with `--remote-debugging-port`, use `find_browser()` to grab the WebSocket URL automatically:

```bash
# First, start Chrome with remote debugging enabled
google-chrome --remote-debugging-port=9222
```

```python
# Then connect to it
url = await LLMBrowser.find_browser()          # scans port 9222 by default
url = await LLMBrowser.find_browser(port=9223) # custom port

async with LLMBrowser(remote_cdp_url=url) as browser:
    state = await browser.get_state()
```

#### Launch & attach to system Chrome

`attach()` finds your system-installed Chrome/Edge, launches it with CDP enabled, and returns a ready `LLMBrowser` — no manual subprocess management needed:

```python
# Fresh temporary profile (default, headless=False so you can see the window)
async with await LLMBrowser.attach() as browser:
    await browser.navigate("https://example.com")

# Use your real Chrome profile — inherits saved logins and cookies
async with await LLMBrowser.attach(
    profile_dir="~/Library/Application Support/Google/Chrome"  # macOS
) as browser:
    await browser.navigate("https://gmail.com")

# Headless with a custom port
async with await LLMBrowser.attach(port=9223, headless=True) as browser:
    state = await browser.get_state()
```

The subprocess is automatically terminated when the `async with` block exits.

#### Remote Browser via CDP

Connect to a browser running on a managed service (Browserbase, Anchor, Bright Data) or your own remote infrastructure instead of launching one locally. Pass the service's CDP WebSocket URL and everything else works identically.

```python
# Browserbase
async with LLMBrowser(remote_cdp_url="wss://connect.browserbase.com?apiKey=YOUR_KEY") as browser:
    await browser.navigate("https://example.com")

# Anchor
async with LLMBrowser(remote_cdp_url="wss://anchor.browser.com/cdp?token=YOUR_TOKEN") as browser:
    await browser.navigate("https://example.com")

# Self-hosted Chromium (e.g. Docker container with --remote-debugging-port=9222)
async with LLMBrowser(remote_cdp_url="ws://localhost:9222") as browser:
    await browser.navigate("https://example.com")
```

**What still works with `remote_cdp_url`:** `stealth`, `session_file`, `viewport`, `user_agent`, `on_captcha_detected`, all 30 tools, HITL, multi-tab.

**What is ignored:** `browser`, `headless`, `slow_mo`, `proxy` (the remote browser controls its own network — a warning is logged if `proxy` is also set).

---

## API Reference

### `LLMBrowser`

```python
LLMBrowser(
    headless: bool = True,
    browser:  BrowserType = BrowserType.CHROME,  # "chrome"|"chromium"|"firefox"|"safari"|"msedge"
    viewport: dict = {"width": 1280, "height": 800},
    timeout:  int  = 30_000,         # ms — page navigation timeout
    user_agent:      str  = None,
    slow_mo:         int  = 0,       # ms delay per action (useful for debugging)
    session_file:    str  = None,    # path to load on startup
    max_retries:     int  = 2,       # retries for click/type on element-not-found
    dismiss_dialogs: bool = True,    # auto-dismiss cookie banners after navigate
    download_dir:    str  = None,    # default: ~/.llmbrowser/downloads/
    stealth:         bool = False,
    proxy:           str | dict = None,
    on_captcha_detected: Callable[[CaptchaContext], Awaitable[str]] = None,
    remote_cdp_url:      str  = None,   # connect to an existing remote browser via CDP
)
```

Used as an async context manager: `async with LLMBrowser(...) as browser:`

Key methods: `navigate`, `get_state`, `execute_action`, `step`, `go_back`, `go_forward`, `reload`, `hover_element`, `select_option`, `manage_storage`, `manage_cookies`, `execute_script`, `get_element_attribute`, `handle_next_dialog`, `new_tab`, `switch_tab`, `close_tab`, `list_tabs`, `save_session`, `load_session`, `detect_captcha`, `inject_captcha_token`, `solve_captcha`, `upload_file`, `read_file`, `page_title`.

### `BrowserToolkit`

```python
BrowserToolkit(
    browser:         LLMBrowser,
    state_mode:      Literal["dom", "aria", "both"] = "dom",
    on_human_needed: Callable[[HumanHelpContext], Awaitable[str]] = None,
    totp_secret:     str = None,         # enables get_totp_code tool (requires llmbrowser[totp])
    tools:           list[str] = None,   # allowlist of tool names; None = all tools
)
```

Key methods: `as_anthropic_tools()`, `as_openai_tools()`, `as_google_tools()`, `as_bedrock_tools()`, `as_langchain_tools()`, `as_llamaindex_tools()`, `get_tools()`, `execute(tool_name, **kwargs)`.

### `PageState`

```python
state.url                   # str  — current URL
state.title                 # str  — page title
state.screenshot_b64        # str  — base64-encoded JPEG screenshot
state.simplified_dom        # str  — DOM tree text (empty when mode="aria")
state.aria_tree             # str | None — ARIA tree text (None when mode="dom")
state.interactive_elements  # list[dict] — [{id, tag, text, rect, in_viewport, …}]
state.scroll_position       # dict — {x, y} in pixels
state.page_height           # int  — total scrollable page height in pixels
```

### Exceptions

| Exception | When raised |
|-----------|------------|
| `NavigationError` | Navigation fails or times out |
| `ElementNotFoundError` | Target element ID not found on page |
| `ActionExecutionError` | Action fails after all retries |
| `PageNotReadyError` | Page is not in a usable state |
| `BrowserToolError` | Base class for all above |

---

## Contributing

Contributions are welcome. Please open an issue before submitting large changes so we can discuss the approach.

```bash
# Clone and install in dev mode
git clone https://github.com/Ponprabhakar-pg/llmbrowser.git
cd llmbrowser
pip install uv
uv sync

# Install Playwright browsers
python -m playwright install chromium

# Run the test suite
pytest tests/ -v

# Unit tests only (no browser needed)
pytest tests/test_unit.py -v
```

---

## License

MIT © [Ponprabhakar-pg](https://github.com/Ponprabhakar-pg)
