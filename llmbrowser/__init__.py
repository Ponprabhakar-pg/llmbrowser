"""
llmbrowser — A framework-agnostic browser tool for AI agents.

Quick start:
    from llmbrowser import LLMBrowser, BrowserToolkit

    async with LLMBrowser() as browser:
        # Default: DOM tree (compatible with all browsers)
        toolkit = BrowserToolkit(browser)

        # Token-efficient: ARIA accessibility tree (~60 % fewer tokens)
        toolkit = BrowserToolkit(browser, state_mode="aria")

        # Both representations (for debugging / comparison)
        toolkit = BrowserToolkit(browser, state_mode="both")

        # Anthropic SDK  (Claude)
        tools = toolkit.as_anthropic_tools()

        # OpenAI SDK  (GPT, DeepSeek, GitHub Copilot, Azure OpenAI,
        #              Groq, Mistral, Together AI, Ollama, LiteLLM …)
        tools = toolkit.as_openai_tools()

        # Google AI SDK  (Gemini via AI Studio or Vertex AI)
        tools = toolkit.as_google_tools()

        # AWS Bedrock Converse API  (boto3)
        tools = toolkit.as_bedrock_tools()

        # LangChain  (requires pip install llmbrowser[langchain])
        tools = toolkit.as_langchain_tools()

        # LlamaIndex  (requires pip install llmbrowser[llamaindex])
        tools = toolkit.as_llamaindex_tools()

        # Any framework — plain async functions
        tools = toolkit.get_tools()

        # Execute a tool by name (use with any provider's tool-call response)
        result = await toolkit.execute("navigate", url="https://example.com")

        # Stealth mode — patch navigator.webdriver, WebGL, plugins, etc.
        async with LLMBrowser(stealth=True) as browser:
            ...

        # Human-in-the-loop — agent calls request_human_help when blocked
        async def my_handler(ctx: HumanHelpContext) -> str:
            return input(f"Agent needs help at {ctx.url}: {ctx.reason}\nYour response: ")

        toolkit = BrowserToolkit(browser, on_human_needed=my_handler)
        # request_human_help tool is automatically added to all adapter outputs

        # Session persistence — save and restore cookies / localStorage
        await browser.save_session("session.json")

        # Multi-tab
        idx = await browser.new_tab("https://example.com")
        await browser.switch_tab(idx)
        tabs = await browser.list_tabs()
        await browser.close_tab()

Playwright browser binaries are installed automatically on first import.
Run  `llmbrowser install`  to install manually.
"""

from .core import LLMBrowser, BrowserType
from ._install import _ensure_chromium_installed
_ensure_chromium_installed()
from .browser_tools import BrowserToolkit
from .schemas import BrowserAction, CaptchaContext, HumanHelpContext, PageState
from .exceptions import (
    BrowserToolError,
    NavigationError,
    ActionExecutionError,
    ElementNotFoundError,
    PageNotReadyError,
)
from .viewport import VIEWPORTS, ViewportResult, run_at_viewports

__version__ = "0.1.0"

__all__ = [
    # Core
    "LLMBrowser",
    "BrowserType",
    "BrowserToolkit",
    # Responsive / multi-viewport
    "VIEWPORTS",
    "ViewportResult",
    "run_at_viewports",
    # Schemas
    "BrowserAction",
    "CaptchaContext",
    "HumanHelpContext",
    "PageState",
    # Exceptions
    "BrowserToolError",
    "NavigationError",
    "ActionExecutionError",
    "ElementNotFoundError",
    "PageNotReadyError",
]
