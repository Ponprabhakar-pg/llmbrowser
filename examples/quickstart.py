"""
llmbrowser — quickstart examples
==================================

Demonstrates the most common usage patterns. No LLM key is needed for the
basic demo; the Anthropic / OpenAI demos require the respective SDK installed.

Usage
-----
    # Basic demo (no API key needed)
    python examples/quickstart.py

    # Anthropic agent loop
    pip install llmbrowser[anthropic]
    python examples/quickstart.py --anthropic

    # OpenAI agent loop
    pip install llmbrowser[openai]
    python examples/quickstart.py --openai

    # Show all page-state modes (dom / aria / both)
    python examples/quickstart.py --modes
"""

import asyncio
import sys

from llmbrowser import LLMBrowser, BrowserToolkit


# ── 1. Basic usage — no LLM required ─────────────────────────────────────────

async def basic_demo() -> None:
    """
    Open a browser, navigate to a page, and print the toolkit's text output.

    BrowserToolkit.execute() returns a human-readable string summarising the
    current page state after each action — URL, title, interactive elements,
    scroll position, etc.
    """
    print("=== Basic Demo ===\n")

    async with LLMBrowser(headless=True) as browser:
        # state_mode="aria" is more token-efficient; use "dom" for full tree
        toolkit = BrowserToolkit(browser, state_mode="aria")

        # navigate returns the page state as a formatted string
        result = await toolkit.execute("navigate", url="https://example.com")
        print(result)


# ── 2. Page-state modes ───────────────────────────────────────────────────────

async def state_modes_demo() -> None:
    """
    Compare the three state representations available via get_state().

    dom   — full simplified DOM tree (verbose, good for debugging)
    aria  — ARIA accessibility tree (~60 % fewer tokens, preferred for agents)
    both  — both representations side by side
    """
    print("=== State Modes Demo ===\n")

    async with LLMBrowser(headless=True) as browser:
        await browser.navigate("https://example.com")

        for mode in ("dom", "aria", "both"):
            state = await browser.get_state(mode=mode)       # type: ignore[arg-type]
            print(f"--- mode={mode} ---")
            print(f"  simplified_dom present : {bool(state.simplified_dom)}")
            print(f"  aria_tree    present   : {state.aria_tree is not None}")
            print(f"  interactive elements   : {len(state.interactive_elements)}")
            print()


# ── 3. Anthropic SDK integration ──────────────────────────────────────────────

async def anthropic_demo() -> None:
    """
    Full agentic loop with the Anthropic Python SDK.

    The toolkit converts all 16 browser tools into Anthropic tool-use format.
    The loop runs until the model emits stop_reason == "end_turn".
    """
    import anthropic                          # pip install llmbrowser[anthropic]

    print("=== Anthropic Agent Demo ===\n")

    client = anthropic.Anthropic()
    messages = [
        {
            "role": "user",
            "content": "Go to example.com and tell me the page title and any links you see.",
        }
    ]

    async with LLMBrowser(headless=True) as browser:
        toolkit = BrowserToolkit(browser, state_mode="aria")
        tools = toolkit.as_anthropic_tools()   # list[dict] with input_schema

        while True:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                tools=tools,
                messages=messages,
            )

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                for block in response.content:
                    if hasattr(block, "text"):
                        print(f"Agent: {block.text}")
                break

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        print(f"  [tool] {block.name}({block.input})")
                        result = await toolkit.execute(block.name, **block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })
                messages.append({"role": "user", "content": tool_results})


# ── 4. OpenAI SDK integration ─────────────────────────────────────────────────

async def openai_demo() -> None:
    """
    Full agentic loop with the OpenAI Python SDK.

    Works with any OpenAI-compatible API: Azure OpenAI, Groq, Mistral,
    DeepSeek, Together AI, Ollama, LiteLLM, etc. — just swap the client.
    """
    import json
    import openai                             # pip install llmbrowser[openai]

    print("=== OpenAI Agent Demo ===\n")

    client = openai.AsyncOpenAI()
    messages = [
        {
            "role": "system",
            "content": "You are a web browsing agent. Use the browser tools to complete tasks.",
        },
        {
            "role": "user",
            "content": "Go to example.com and tell me the page title and any links you see.",
        },
    ]

    async with LLMBrowser(headless=True) as browser:
        toolkit = BrowserToolkit(browser, state_mode="aria")
        tools = toolkit.as_openai_tools()     # list[dict] with type="function"

        while True:
            response = await client.chat.completions.create(
                model="gpt-4o",
                tools=tools,
                messages=messages,
            )

            msg = response.choices[0].message
            messages.append(msg)

            if response.choices[0].finish_reason == "stop":
                print(f"Agent: {msg.content}")
                break

            if response.choices[0].finish_reason == "tool_calls":
                tool_results = []
                for tc in msg.tool_calls:
                    print(f"  [tool] {tc.function.name}({tc.function.arguments})")
                    kwargs = json.loads(tc.function.arguments)
                    result = await toolkit.execute(tc.function.name, **kwargs)
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
                messages.extend(tool_results)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--anthropic" in sys.argv:
        asyncio.run(anthropic_demo())
    elif "--openai" in sys.argv:
        asyncio.run(openai_demo())
    elif "--modes" in sys.argv:
        asyncio.run(state_modes_demo())
    else:
        asyncio.run(basic_demo())
