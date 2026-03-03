import logging
import uuid
from typing import Callable, Literal, Optional

StateMode = Literal["dom", "aria", "both"]

from .core import LLMBrowser
from .schemas import HumanHelpContext, PageState

logger = logging.getLogger(__name__)


def _spec(fn, description: str, parameters: dict):
    """Attach schema metadata to a callable and return it."""
    fn._tool_description = description
    fn._tool_parameters  = parameters
    return fn


class BrowserToolkit:
    """
    Exposes LLMBrowser capabilities as callable tools for AI agents.

    Supports four integration styles:

    1. Framework-agnostic (plain async functions):
        tools = toolkit.get_tools()

    2. Anthropic SDK:
        response = client.messages.create(tools=toolkit.as_anthropic_tools(), ...)
        result = await toolkit.execute(block.name, **block.input)

    3. OpenAI SDK (also works with DeepSeek, Azure OpenAI, Groq, Mistral, Ollama, LiteLLM):
        response = client.chat.completions.create(tools=toolkit.as_openai_tools(), ...)
        result = await toolkit.execute(fn.name, **json.loads(fn.arguments))

    4. Google AI (Gemini via AI Studio or Vertex AI):
        model = genai.GenerativeModel("gemini-2.0-flash", tools=toolkit.as_google_tools())

    5. AWS Bedrock Converse API (boto3):
        response = client.converse(toolConfig={"tools": toolkit.as_bedrock_tools()}, ...)

    6. LangChain (requires pip install llmbrowser[langchain]):
        tools = toolkit.as_langchain_tools()
        agent = create_tool_calling_agent(llm, tools, prompt)

    7. LlamaIndex (requires pip install llmbrowser[llamaindex]):
        tools = toolkit.as_llamaindex_tools()
        agent = ReActAgent.from_tools(tools, llm=llm, verbose=True)
    """

    BASE_TOOLS: frozenset = frozenset({
        "navigate", "get_page_state", "click_element", "type_text",
        "press_key", "scroll_page", "go_back", "go_forward", "reload_page",
        "hover_element", "select_option", "wait_for_page",
        "new_tab", "switch_tab", "list_tabs", "close_tab",
        "upload_file", "get_downloads", "read_file", "dismiss_dialogs",
        "manage_storage", "manage_cookies", "execute_script",
        "get_element_attribute", "handle_dialog",
        "drag_and_drop", "take_element_screenshot",
        "switch_frame", "set_geolocation", "export_pdf",
        "get_bookmarks", "get_history",
    })

    def __init__(
        self,
        browser: LLMBrowser,
        state_mode: StateMode = "dom",
        on_human_needed: Optional[Callable] = None,
        totp_secret: Optional[str] = None,
        tools: Optional[list] = None,
    ):
        self._browser          = browser
        self._state_mode       = state_mode
        self._on_human_needed  = on_human_needed
        self._totp_secret      = totp_secret
        self._session_id       = str(uuid.uuid4())
        self._tools_filter     = set(tools) if tools is not None else None

        if tools is not None:
            # Validate against all possible tool names (base + optional)
            all_possible = self.BASE_TOOLS | {"solve_captcha", "get_totp_code", "request_human_help"}
            unknown = set(tools) - all_possible
            if unknown:
                raise ValueError(
                    f"Unknown tool name(s): {unknown}. "
                    f"Valid base tool names: {self.BASE_TOOLS}"
                )

        self._tools            = self._build_tools()
        self._tool_map: dict[str, Callable] = {fn.__name__: fn for fn in self._tools}

    # ------------------------------------------------------------------
    # Integration interfaces
    # ------------------------------------------------------------------

    def get_tools(self) -> list:
        """Return plain async functions — works with any framework."""
        return self._tools

    def _all_schemas(self) -> list[dict]:
        """Derive schemas from function attributes on self._tools."""
        return [
            {
                "name":        fn.__name__,
                "description": fn._tool_description,
                "parameters":  fn._tool_parameters,
            }
            for fn in self._tools
        ]

    def as_anthropic_tools(self) -> list[dict]:
        """Return tool definitions formatted for the Anthropic Python SDK."""
        return [
            {
                "name":         t["name"],
                "description":  t["description"],
                "input_schema": t["parameters"],
            }
            for t in self._all_schemas()
        ]

    def as_openai_tools(self) -> list[dict]:
        """
        Return tool definitions formatted for the OpenAI Python SDK.

        Also works with: DeepSeek, GitHub Copilot (GitHub Models), Azure OpenAI,
        Groq, Together AI, Mistral, Ollama, and any OpenAI-compatible API.
        Also works with LiteLLM (it converts to provider format internally).
        """
        return [
            {
                "type": "function",
                "function": {
                    "name":        t["name"],
                    "description": t["description"],
                    "parameters":  t["parameters"],
                },
            }
            for t in self._all_schemas()
        ]

    def as_google_tools(self) -> list[dict]:
        """
        Return tool definitions formatted for Google AI SDKs.

        Works with:
        - google-generativeai (Google AI Studio / Gemini API)
        - vertexai (Google Cloud Vertex AI with Gemini models)
        """
        return [
            {
                "function_declarations": [
                    {
                        "name":        t["name"],
                        "description": t["description"],
                        "parameters":  t["parameters"],
                    }
                    for t in self._all_schemas()
                ]
            }
        ]

    def as_bedrock_tools(self) -> list[dict]:
        """
        Return tool definitions for the AWS Bedrock Converse API (boto3).

        Usage:
            client = boto3.client("bedrock-runtime")
            response = client.converse(
                modelId="anthropic.claude-sonnet-4-5-v1:0",
                messages=[...],
                toolConfig={"tools": toolkit.as_bedrock_tools()},
            )
            for item in response["output"]["message"]["content"]:
                if item.get("toolUse"):
                    tc = item["toolUse"]
                    result = await toolkit.execute(tc["name"], **tc["input"])
        """
        return [
            {
                "toolSpec": {
                    "name":        t["name"],
                    "description": t["description"],
                    "inputSchema": {"json": t["parameters"]},
                }
            }
            for t in self._all_schemas()
        ]

    def as_langchain_tools(self) -> list:
        """
        Return tools as LangChain ``StructuredTool`` objects.

        Requires: ``pip install 'llmbrowser[langchain]'``

        Usage::

            from langchain.agents import AgentExecutor, create_tool_calling_agent
            from langchain_core.prompts import ChatPromptTemplate

            tools  = toolkit.as_langchain_tools()
            prompt = ChatPromptTemplate.from_messages([...])
            agent  = create_tool_calling_agent(llm, tools, prompt)
            runner = AgentExecutor(agent=agent, tools=tools)

        Tool schemas are inferred from Python type annotations — no manual
        JSON-Schema conversion required.
        """
        try:
            from langchain_core.tools import StructuredTool
        except ImportError as exc:
            raise ImportError(
                "LangChain integration requires langchain-core. "
                "Install it with: pip install 'llmbrowser[langchain]'"
            ) from exc

        return [
            StructuredTool.from_function(
                coroutine=fn,
                name=fn.__name__,
                description=fn._tool_description,
            )
            for fn in self._tools
        ]

    def as_llamaindex_tools(self) -> list:
        """
        Return tools as LlamaIndex ``FunctionTool`` objects.

        Requires: ``pip install 'llmbrowser[llamaindex]'``

        Usage::

            from llama_index.core.agent import ReActAgent

            tools = toolkit.as_llamaindex_tools()
            agent = ReActAgent.from_tools(tools, llm=llm, verbose=True)

        Tool schemas are inferred from Python type annotations — no manual
        JSON-Schema conversion required.
        """
        try:
            from llama_index.core.tools import FunctionTool
        except ImportError as exc:
            raise ImportError(
                "LlamaIndex integration requires llama-index-core. "
                "Install it with: pip install 'llmbrowser[llamaindex]'"
            ) from exc

        return [
            FunctionTool.from_defaults(
                async_fn=fn,
                name=fn.__name__,
                description=fn._tool_description,
            )
            for fn in self._tools
        ]

    async def execute(self, tool_name: str, **kwargs) -> str:
        """
        Execute a tool by name.

        Use this when handling tool calls from an LLM response:
            result = await toolkit.execute(block.name, **block.input)

        Raises:
            ValueError: if tool_name is not recognised or not in the active allowlist.
        """
        fn = self._tool_map.get(tool_name)
        if fn is None:
            available = list(self._tool_map)
            raise ValueError(f"Unknown tool '{tool_name}'. Available: {available}")
        return await fn(**kwargs)

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    def _build_tools(self) -> list:
        browser = self._browser
        mode    = self._state_mode

        async def navigate(url: str) -> str:
            await browser.navigate(url)
            state = await browser.get_state(mode=mode)
            return _format_state(state)
        navigate = _spec(navigate,
            description=(
                "Navigate the browser to a URL and return the current page state. "
                "Use this to open a website or move to a new page. "
                "Always call this first before interacting with any page elements."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The full URL to navigate to (e.g. 'https://amazon.com').",
                    },
                },
                "required": ["url"],
            },
        )

        async def get_page_state() -> str:
            state = await browser.get_state(mode=mode)
            return _format_state(state)
        get_page_state = _spec(get_page_state,
            description=(
                "Get the current state of the browser page. "
                "Use this to observe what is currently visible before acting. "
                "Returns interactive element IDs, page title, URL, and scroll position."
            ),
            parameters={"type": "object", "properties": {}},
        )

        async def click_element(element_id: int) -> str:
            state = await browser.step(
                {"thought": f"Clicking element id={element_id}", "action": "click", "target_id": element_id},
                mode=mode,
            )
            return _format_state(state)
        click_element = _spec(click_element,
            description=(
                "Click an interactive element on the page by its numeric ID. "
                "Use this to click buttons, links, checkboxes, or any clickable element. "
                "The element_id comes from the page state."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "element_id": {
                        "type": "integer",
                        "description": "The numeric ID shown in page state, e.g. 3 for [3].",
                    },
                },
                "required": ["element_id"],
            },
        )

        async def type_text(element_id: int, text: str) -> str:
            state = await browser.step(
                {"thought": f"Typing into element id={element_id}", "action": "type", "target_id": element_id, "value": text},
                mode=mode,
            )
            return _format_state(state)
        type_text = _spec(type_text,
            description=(
                "Type text into an input field, textarea, or search box. "
                "Clears existing content before typing. "
                "Use this to fill forms, search boxes, or any text input."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "element_id": {
                        "type": "integer",
                        "description": "The numeric ID of the input element.",
                    },
                    "text": {
                        "type": "string",
                        "description": "The text to type.",
                    },
                },
                "required": ["element_id", "text"],
            },
        )

        async def press_key(key: str = "Enter") -> str:
            state = await browser.step(
                {"thought": f"Pressing key: {key}", "action": "key", "value": key},
                mode=mode,
            )
            return _format_state(state)
        press_key = _spec(press_key,
            description=(
                "Press a keyboard key or key combination. "
                "Use this to submit forms (Enter), navigate between fields (Tab), "
                "dismiss dialogs (Escape), or trigger keyboard shortcuts."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": (
                            "Key name, e.g. 'Enter', 'Tab', 'Escape', 'ArrowDown', "
                            "'Control+a', 'Meta+k'."
                        ),
                    },
                },
                "required": ["key"],
            },
        )

        async def scroll_page(
            direction: Literal["up", "down", "left", "right"] = "down",
            amount: int = 300,
        ) -> str:
            state = await browser.step(
                {"thought": f"Scrolling {direction} by {amount}px", "action": "scroll",
                 "scroll_direction": direction, "scroll_amount": amount},
                mode=mode,
            )
            return _format_state(state)
        scroll_page = _spec(scroll_page,
            description=(
                "Scroll the page to reveal more content. "
                "Use this when needed elements are not visible in the current viewport."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down", "left", "right"],
                        "description": "Direction to scroll. Default: 'down'.",
                    },
                    "amount": {
                        "type": "integer",
                        "description": "Pixels to scroll. Default: 300.",
                        "minimum": 1,
                        "maximum": 5000,
                    },
                },
            },
        )

        async def go_back() -> str:
            await browser.go_back()
            state = await browser.get_state(mode=mode)
            return _format_state(state)
        go_back = _spec(go_back,
            description=(
                "Navigate back to the previous page in browser history. "
                "Use this to return to a previous page after following a wrong link."
            ),
            parameters={"type": "object", "properties": {}},
        )

        async def wait_for_page(milliseconds: int = 2000) -> str:
            state = await browser.step(
                {"thought": f"Waiting {milliseconds}ms for page to settle", "action": "wait", "value": str(milliseconds)},
                mode=mode,
            )
            return _format_state(state)
        wait_for_page = _spec(wait_for_page,
            description=(
                "Wait for a specified time then return the current page state. "
                "Use this when a page is loading dynamic content or processing a form submission."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "milliseconds": {
                        "type": "integer",
                        "description": "Time to wait in milliseconds. Min: 500, Max: 10000. Default: 2000.",
                        "minimum": 500,
                        "maximum": 10000,
                    },
                },
            },
        )

        async def new_tab(url: Optional[str] = None) -> str:
            idx = await browser.new_tab(url)
            if url:
                state = await browser.get_state(mode=mode)
                return f"Opened tab {idx}.\n" + _format_state(state)
            return f"Opened blank tab {idx}."
        new_tab = _spec(new_tab,
            description=(
                "Open a new browser tab, optionally navigating to a URL, and switch to it. "
                "Use this to work with multiple pages simultaneously."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Optional URL to open in the new tab.",
                    },
                },
            },
        )

        async def switch_tab(index: int) -> str:
            await browser.switch_tab(index)
            state = await browser.get_state(mode=mode)
            return f"Switched to tab {index}.\n" + _format_state(state)
        switch_tab = _spec(switch_tab,
            description=(
                "Switch to a different browser tab by its index. "
                "Use list_tabs to see all open tabs and their indices."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "index": {
                        "type": "integer",
                        "description": "Zero-based index of the tab to switch to.",
                        "minimum": 0,
                    },
                },
                "required": ["index"],
            },
        )

        async def list_tabs() -> str:
            tabs = await browser.list_tabs()
            lines = ["OPEN TABS:"]
            for t in tabs:
                marker = " ◀ active" if t["active"] else ""
                lines.append(f"  [{t['index']}] {t['title']} — {t['url']}{marker}")
            return "\n".join(lines)
        list_tabs = _spec(list_tabs,
            description=(
                "List all open browser tabs with their index, URL, title, and active status. "
                "Use this before switch_tab to find the right tab index."
            ),
            parameters={"type": "object", "properties": {}},
        )

        async def close_tab() -> str:
            await browser.close_tab()
            state = await browser.get_state(mode=mode)
            return f"Closed tab. Now on tab {browser.active_tab}.\n" + _format_state(state)
        close_tab = _spec(close_tab,
            description=(
                "Close the current browser tab. "
                "Cannot close the last remaining tab."
            ),
            parameters={"type": "object", "properties": {}},
        )

        async def dismiss_dialogs() -> str:
            dismissed = await browser.dismiss_cookie_banners()
            if dismissed:
                state = await browser.get_state(mode=mode)
                return "Dialog dismissed.\n" + _format_state(state)
            return "No dismissible cookie/consent dialog found on this page."
        dismiss_dialogs = _spec(dismiss_dialogs,
            description=(
                "Dismiss cookie consent banners, GDPR dialogs, and popup overlays. "
                "Call this if the page has a consent dialog blocking interaction. "
                "Banners are dismissed automatically after navigation by default, "
                "but use this if one appears after a dynamic page transition."
            ),
            parameters={"type": "object", "properties": {}},
        )

        async def upload_file(element_id: int, file_path: str) -> str:
            await browser.upload_file(element_id, file_path)
            state = await browser.get_state(mode=mode)
            return f"Uploaded '{file_path}' to element id={element_id}.\n" + _format_state(state)
        upload_file = _spec(upload_file,
            description=(
                "Upload a file from the local filesystem to a file-input element. "
                "Use this to attach files to forms or upload dialogs. "
                "The element_id must be a file input (<input type='file'>)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "element_id": {
                        "type": "integer",
                        "description": "Numeric ID of the file-input element from page state.",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Absolute path to the file to upload.",
                    },
                },
                "required": ["element_id", "file_path"],
            },
        )

        async def get_downloads() -> str:
            files = browser.downloads
            if not files:
                return "No files have been downloaded in this session."
            lines = [f"DOWNLOADED FILES ({len(files)}):"]
            for i, path in enumerate(files, 1):
                p = __import__("pathlib").Path(path)
                size = p.stat().st_size if p.exists() else 0
                lines.append(f"  [{i}] {p.name}  ({size:,} bytes)  — {path}")
            return "\n".join(lines)
        get_downloads = _spec(get_downloads,
            description=(
                "List all files downloaded during this session. "
                "Returns file paths and names. Use read_file to access their content."
            ),
            parameters={"type": "object", "properties": {}},
        )

        async def read_file(path: str) -> str:
            return browser.read_file(path)
        read_file = _spec(read_file,
            description=(
                "Read the text content of a local or downloaded file. "
                "Supports: txt, csv, json, md, html, xml, yaml, log, tsv. "
                "Returns a metadata summary for binary or unsupported file types."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path, typically from get_downloads.",
                    },
                },
                "required": ["path"],
            },
        )

        async def go_forward() -> str:
            await browser.go_forward()
            state = await browser.get_state(mode=mode)
            return _format_state(state)
        go_forward = _spec(go_forward,
            description=(
                "Navigate forward to the next page in browser history. "
                "Use this to redo a go_back navigation."
            ),
            parameters={"type": "object", "properties": {}},
        )

        async def reload_page() -> str:
            await browser.reload()
            state = await browser.get_state(mode=mode)
            return _format_state(state)
        reload_page = _spec(reload_page,
            description=(
                "Reload the current page. "
                "Use this to refresh stale content or retry after a network hiccup."
            ),
            parameters={"type": "object", "properties": {}},
        )

        async def hover_element(element_id: int) -> str:
            await browser.hover_element(element_id)
            state = await browser.get_state(mode=mode)
            return _format_state(state)
        hover_element = _spec(hover_element,
            description=(
                "Hover the mouse over an element to reveal tooltips, dropdown menus, "
                "or other hover-triggered content. Use get_page_state after to see what appeared."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "element_id": {
                        "type": "integer",
                        "description": "Numeric ID of the element to hover over.",
                    },
                },
                "required": ["element_id"],
            },
        )

        async def select_option(
            element_id: int,
            value: Optional[str] = None,
            label: Optional[str] = None,
            index: Optional[int] = None,
        ) -> str:
            selected = await browser.select_option(element_id, value=value, label=label, index=index)
            state = await browser.get_state(mode=mode)
            return f"Selected: {selected}\n" + _format_state(state)
        select_option = _spec(select_option,
            description=(
                "Select an option in a <select> dropdown element. "
                "Provide exactly one of: value (option value attr), label (visible text), "
                "or index (0-based position)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "element_id": {
                        "type": "integer",
                        "description": "Numeric ID of the <select> element.",
                    },
                    "value": {
                        "type": "string",
                        "description": "The option's value attribute to select.",
                    },
                    "label": {
                        "type": "string",
                        "description": "The option's visible label text to select.",
                    },
                    "index": {
                        "type": "integer",
                        "description": "Zero-based index of the option to select.",
                        "minimum": 0,
                    },
                },
                "required": ["element_id"],
            },
        )

        async def manage_storage(
            store: Literal["local", "session"],
            action: Literal["get", "set", "remove", "clear", "getall"],
            key: Optional[str] = None,
            value: Optional[str] = None,
        ) -> str:
            return await browser.manage_storage(store, action, key=key, value=value)
        manage_storage = _spec(manage_storage,
            description=(
                "Read or write browser localStorage or sessionStorage. "
                "Actions: 'get' (read one key), 'set' (write key+value), "
                "'remove' (delete one key), 'clear' (wipe all), 'getall' (dump all as JSON)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "store": {
                        "type": "string",
                        "enum": ["local", "session"],
                        "description": "'local' for localStorage, 'session' for sessionStorage.",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["get", "set", "remove", "clear", "getall"],
                        "description": "Operation to perform.",
                    },
                    "key": {
                        "type": "string",
                        "description": "Storage key. Required for get, set, remove.",
                    },
                    "value": {
                        "type": "string",
                        "description": "Value to store. Required for set.",
                    },
                },
                "required": ["store", "action"],
            },
        )

        async def manage_cookies(
            action: Literal["list", "set", "delete"],
            name: Optional[str] = None,
            value: Optional[str] = None,
            path: str = "/",
        ) -> str:
            return await browser.manage_cookies(action, name=name, value=value, path=path)
        manage_cookies = _spec(manage_cookies,
            description=(
                "List, set, or delete browser cookies for the current context. "
                "Actions: 'list' (show all cookies), 'set' (add/update a cookie), "
                "'delete' (remove a cookie by name)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "set", "delete"],
                        "description": "Operation to perform.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Cookie name. Required for set and delete.",
                    },
                    "value": {
                        "type": "string",
                        "description": "Cookie value. Required for set.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Cookie path. Default: '/'.",
                    },
                },
                "required": ["action"],
            },
        )

        async def execute_script(script: str) -> str:
            return await browser.execute_script(script)
        execute_script = _spec(execute_script,
            description=(
                "Execute arbitrary JavaScript on the current page and return the result. "
                "Use this for operations not covered by other tools. "
                "The script runs in the page context — return a value to get output."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "script": {
                        "type": "string",
                        "description": (
                            "JavaScript to evaluate. Return a value to receive output, "
                            "e.g. '() => document.title' or '() => window.myVar'."
                        ),
                    },
                },
                "required": ["script"],
            },
        )

        async def get_element_attribute(element_id: int, attribute: str) -> str:
            return await browser.get_element_attribute(element_id, attribute)
        get_element_attribute = _spec(get_element_attribute,
            description=(
                "Read a specific HTML attribute from an element by its numeric ID. "
                "Use this to get href, src, value, placeholder, data-*, aria-*, "
                "or any other attribute without fetching the full page state."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "element_id": {
                        "type": "integer",
                        "description": "Numeric ID of the element from page state.",
                    },
                    "attribute": {
                        "type": "string",
                        "description": (
                            "Attribute name to read, e.g. 'href', 'src', 'value', "
                            "'data-id', 'aria-label', 'placeholder'."
                        ),
                    },
                },
                "required": ["element_id", "attribute"],
            },
        )

        async def handle_dialog(
            action: Literal["accept", "dismiss"],
            prompt_text: str = "",
        ) -> str:
            browser.handle_next_dialog(action, prompt_text=prompt_text)
            return (
                f"Dialog handler registered — will {action} the next alert/confirm/prompt. "
                "Now trigger the action that causes the dialog to appear."
            )
        handle_dialog = _spec(handle_dialog,
            description=(
                "Pre-register a handler for the next native browser dialog "
                "(alert, confirm, or prompt). "
                "Call this BEFORE the action that triggers the dialog. "
                "Use action='accept' to click OK (optionally with prompt_text for prompt dialogs), "
                "or action='dismiss' to click Cancel."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["accept", "dismiss"],
                        "description": "'accept' clicks OK, 'dismiss' clicks Cancel.",
                    },
                    "prompt_text": {
                        "type": "string",
                        "description": "Text to type into a prompt() dialog before accepting. Ignored for alert/confirm.",
                    },
                },
                "required": ["action"],
            },
        )

        async def drag_and_drop(source_id: int, target_id: int) -> str:
            await browser.drag_and_drop(source_id, target_id)
            state = await browser.get_state(mode=mode)
            return _format_state(state)
        drag_and_drop = _spec(drag_and_drop,
            description=(
                "Drag one element onto another. "
                "Use this for Kanban cards, sortable lists, file-drop zones, "
                "sliders, and any drag-and-drop interaction."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source_id": {
                        "type": "integer",
                        "description": "Numeric ID of the element to drag.",
                    },
                    "target_id": {
                        "type": "integer",
                        "description": "Numeric ID of the element to drop onto.",
                    },
                },
                "required": ["source_id", "target_id"],
            },
        )

        async def take_element_screenshot(element_id: int) -> str:
            b64 = await browser.take_element_screenshot(element_id)
            return f"Element screenshot (base64 PNG, {len(b64)} chars): {b64[:80]}…"
        take_element_screenshot = _spec(take_element_screenshot,
            description=(
                "Take a screenshot of a single element and return it as a base64-encoded PNG. "
                "Use this to visually inspect a chart, image, or specific UI component "
                "without capturing the whole page."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "element_id": {
                        "type": "integer",
                        "description": "Numeric ID of the element to screenshot.",
                    },
                },
                "required": ["element_id"],
            },
        )

        async def switch_frame(
            frame_id: Optional[str] = None,
            url_pattern: Optional[str] = None,
        ) -> str:
            return await browser.switch_frame(frame_id=frame_id, url_pattern=url_pattern)
        switch_frame = _spec(switch_frame,
            description=(
                "List all iframes on the current page. "
                "Use this to discover embedded widgets (payment forms, maps, videos). "
                "Elements inside iframes are already annotated by get_page_state — "
                "you do not need to switch context to interact with them."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "frame_id": {
                        "type": "string",
                        "description": "Optional frame ID to get details about a specific frame.",
                    },
                    "url_pattern": {
                        "type": "string",
                        "description": "Optional URL substring to filter frames by.",
                    },
                },
            },
        )

        async def set_geolocation(
            latitude: float,
            longitude: float,
            accuracy: float = 100.0,
        ) -> str:
            await browser.set_geolocation(latitude, longitude, accuracy=accuracy)
            return (
                f"Geolocation set to lat={latitude}, lon={longitude} "
                f"(accuracy={accuracy}m). Reload the page for location-aware content to update."
            )
        set_geolocation = _spec(set_geolocation,
            description=(
                "Override the browser's reported GPS location. "
                "Use this before visiting location-aware pages (store finders, "
                "weather apps, maps). Call reload_page after to apply the new location."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "latitude": {
                        "type": "number",
                        "description": "Latitude in decimal degrees (e.g. 37.7749 for San Francisco).",
                        "minimum": -90,
                        "maximum": 90,
                    },
                    "longitude": {
                        "type": "number",
                        "description": "Longitude in decimal degrees (e.g. -122.4194 for San Francisco).",
                        "minimum": -180,
                        "maximum": 180,
                    },
                    "accuracy": {
                        "type": "number",
                        "description": "Accuracy in metres. Default: 100.",
                        "minimum": 0,
                    },
                },
                "required": ["latitude", "longitude"],
            },
        )

        async def export_pdf(path: Optional[str] = None) -> str:
            saved_path = await browser.export_pdf(path=path)
            return f"PDF saved to: {saved_path}"
        export_pdf = _spec(export_pdf,
            description=(
                "Export the current page as a PDF file. "
                "Returns the path where the PDF was saved. "
                "Only works with Chromium in headless mode."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "File path to save the PDF. "
                            "If omitted, saved to ~/.llmbrowser/downloads/export_<timestamp>.pdf"
                        ),
                    },
                },
            },
        )

        async def get_bookmarks(folder: Optional[str] = None) -> str:
            return await browser.get_bookmarks(folder=folder)
        get_bookmarks = _spec(get_bookmarks,
            description=(
                "Read bookmarks from the connected browser profile. "
                "Returns all bookmarks with title, URL, and folder path. "
                "Only works when connected via attach() or connect_or_attach() with user_data_dir set. "
                "Use the folder parameter to filter by a specific bookmark folder name."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "folder": {
                        "type": "string",
                        "description": "Optional folder name to filter by (case-insensitive substring match). Omit to return all bookmarks.",
                    },
                },
            },
        )

        async def get_history(limit: int = 50, search: Optional[str] = None) -> str:
            return await browser.get_history(limit=limit, search=search)
        get_history = _spec(get_history,
            description=(
                "Read recent browser history from the connected profile. "
                "Returns page titles, URLs, last visit times, and visit counts. "
                "Only works when connected via attach() or connect_or_attach() with user_data_dir set."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of history entries to return (default 50, max 500).",
                    },
                    "search": {
                        "type": "string",
                        "description": "Optional substring to filter results by URL or page title.",
                    },
                },
            },
        )

        tools = [
            navigate, get_page_state, click_element, type_text, press_key,
            scroll_page, go_back, go_forward, reload_page, hover_element,
            select_option, wait_for_page,
            new_tab, switch_tab, list_tabs, close_tab,
            dismiss_dialogs, upload_file, get_downloads, read_file,
            manage_storage, manage_cookies, execute_script,
            get_element_attribute, handle_dialog,
            drag_and_drop, take_element_screenshot,
            switch_frame, set_geolocation, export_pdf,
            get_bookmarks, get_history,
        ]

        # TOTP tool — only included when a TOTP secret is configured.
        if self._totp_secret:
            totp_secret = self._totp_secret

            async def get_totp_code() -> str:
                """Generate the current TOTP code for 2FA login."""
                try:
                    import pyotp
                except ImportError as exc:
                    raise ImportError(
                        "TOTP support requires pyotp. "
                        "Install it with: pip install 'llmbrowser[totp]'"
                    ) from exc
                code = pyotp.TOTP(totp_secret).now()
                seconds_remaining = 30 - (__import__("time").time() % 30)
                logger.info("TOTP code generated — expires in %.0fs", seconds_remaining)
                return (
                    f"TOTP code: {code}\n"
                    f"Valid for approximately {seconds_remaining:.0f} more seconds. "
                    "Type this into the OTP field immediately."
                )
            get_totp_code = _spec(get_totp_code,
                description=(
                    "Generate the current TOTP (Time-based One-Time Password) code for 2FA / MFA login. "
                    "Call this when a login form asks for an authenticator app code, OTP, or verification code. "
                    "Returns a 6-digit code that is valid for the current 30-second window. "
                    "Type the returned code into the OTP input field immediately — it expires in 30 seconds."
                ),
                parameters={"type": "object", "properties": {}},
            )
            tools.append(get_totp_code)

        # CAPTCHA tool — only included when the browser has a solver callback.
        if browser._on_captcha_detected:
            async def solve_captcha() -> str:
                """Detect and solve a CAPTCHA on the current page."""
                solved = await browser.solve_captcha()
                if not solved:
                    return "No CAPTCHA detected on this page."
                state = await browser.get_state(mode=mode)
                return "CAPTCHA solved.\n" + _format_state(state)
            solve_captcha = _spec(solve_captcha,
                description=(
                    "Detect and solve a CAPTCHA on the current page using the configured solver. "
                    "Call this if you see a CAPTCHA challenge that is blocking interaction. "
                    "CAPTCHAs are also solved automatically after every navigate call."
                ),
                parameters={"type": "object", "properties": {}},
            )
            tools.append(solve_captcha)

        # HITL tool — only included when the developer has provided a callback.
        if self._on_human_needed:
            on_human_needed = self._on_human_needed
            session_id      = self._session_id

            async def request_human_help(reason: str) -> str:
                """
                Ask a human operator for help when the agent is blocked.

                Captures a screenshot of the current page, passes full context
                to the developer-supplied on_human_needed callback, and returns
                the human's response to the agent.
                """
                state = await browser.get_state(mode="dom")
                ctx   = HumanHelpContext(
                    reason=reason,
                    url=state.url,
                    title=state.title,
                    screenshot_b64=state.screenshot_b64,
                    session_id=session_id,
                )
                logger.info("HITL requested — reason: %s | url: %s", reason, state.url)
                response = await on_human_needed(ctx)
                logger.info("HITL response received.")
                return f"Human responded: {response}"
            request_human_help = _spec(request_human_help,
                description=(
                    "Ask a human operator for help when you are blocked and cannot proceed autonomously. "
                    "Use this when you encounter a CAPTCHA, MFA prompt, login wall, payment confirmation, "
                    "or any situation that requires human judgment or action. "
                    "Clearly describe what you need in the reason field — the human will see a screenshot "
                    "and your explanation, then provide a response you can act on."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": (
                                "Explain exactly what you need the human to do or provide. "
                                "Be specific — 'MFA code required on /login/verify' is better than 'stuck'."
                            ),
                        },
                    },
                    "required": ["reason"],
                },
            )
            tools.append(request_human_help)

        # Apply allowlist filter if specified.
        if self._tools_filter is not None:
            tools = [t for t in tools if t.__name__ in self._tools_filter]

        return tools


# ------------------------------------------------------------------
# State formatter
# ------------------------------------------------------------------


def _format_state(state: PageState) -> str:
    lines = [
        f"URL: {state.url}",
        f"Title: {state.title}",
        f"Scroll: {state.scroll_position.get('y', 0)}px / {state.page_height}px",
        "",
        "INTERACTIVE ELEMENTS:",
    ]

    viewport_els = [e for e in state.interactive_elements if e.get("in_viewport")]

    if not viewport_els:
        lines.append("  (none visible — try scrolling)")
    else:
        for el in viewport_els:
            parts = [f"  [{el['id']}] <{el['tag']}>"]
            if el.get("frame_id"):    parts.append(f"[iframe:{el['frame_id']}]")
            if el.get("type"):        parts.append(f"type={el['type']}")
            if el.get("text"):        parts.append(f'"{el["text"][:50]}"')
            if el.get("aria_label"):  parts.append(f"aria={el['aria_label'][:40]}")
            if el.get("placeholder"): parts.append(f"hint={el['placeholder'][:40]}")
            if el.get("href"):        parts.append(f"href={el['href'][:60]}")
            lines.append(" ".join(parts))

    offscreen = sum(1 for e in state.interactive_elements if not e.get("in_viewport"))
    if offscreen:
        lines.append(f"\n  (+{offscreen} off-screen, scroll to reveal)")

    # Prefer ARIA tree (more compact) when available; fall back to simplified DOM.
    if state.aria_tree:
        lines += ["", "ARIA TREE:", state.aria_tree[:4000]]
        if len(state.aria_tree) > 4000:
            lines.append("  ...(truncated)")
    if state.simplified_dom:
        lines += ["", "PAGE STRUCTURE:", state.simplified_dom[:3000]]
        if len(state.simplified_dom) > 3000:
            lines.append("  ...(truncated)")

    return "\n".join(lines)
