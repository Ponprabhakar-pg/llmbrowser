"""
llmbrowser — framework adapter examples
=========================================

llmbrowser works with every major AI framework out of the box. The
BrowserToolkit class converts the same 16 browser tools into each framework's
required format. You only write the agent loop differently — the browser
interaction code stays the same.

Supported frameworks and their install commands
------------------------------------------------
    pip install llmbrowser[anthropic]      # Anthropic Claude (see quickstart.py)
    pip install llmbrowser[openai]         # OpenAI & compatible APIs (see quickstart.py)
    pip install llmbrowser[google]         # Google Gemini (Gemini API & Vertex AI)
    pip install llmbrowser[bedrock]        # AWS Bedrock (Claude via Bedrock)
    pip install llmbrowser[langchain]      # LangChain
    pip install llmbrowser[llamaindex]     # LlamaIndex
    pip install llmbrowser[all]            # everything above

Usage
-----
    python examples/frameworks.py --google
    python examples/frameworks.py --bedrock
    python examples/frameworks.py --langchain
    python examples/frameworks.py --llamaindex
    python examples/frameworks.py --formats    # print tool schema formats, no LLM needed
"""

import asyncio
import sys

from llmbrowser import LLMBrowser, BrowserToolkit


# ── 0. Inspect tool schema formats (no API key required) ─────────────────────

async def show_formats_demo() -> None:
    """
    Print the first tool in each adapter format so you can see exactly what
    each framework receives. Useful for debugging adapter integration.
    """
    print("=== Tool Schema Formats Demo ===\n")

    async with LLMBrowser(headless=True) as browser:
        toolkit = BrowserToolkit(browser)

        print("--- Anthropic format (input_schema) ---")
        t = toolkit.as_anthropic_tools()[0]
        print(f"  name         : {t['name']}")
        print(f"  keys         : {list(t.keys())}")
        print(f"  schema type  : {t['input_schema']['type']}\n")

        print("--- OpenAI format (function wrapper) ---")
        t = toolkit.as_openai_tools()[0]
        print(f"  type         : {t['type']}")
        print(f"  function keys: {list(t['function'].keys())}\n")

        print("--- Google format (function_declarations) ---")
        g = toolkit.as_google_tools()
        print(f"  top-level keys       : {list(g[0].keys())}")
        print(f"  function_declarations: {len(g[0]['function_declarations'])} tools\n")

        print("--- Bedrock format (toolSpec) ---")
        t = toolkit.as_bedrock_tools()[0]
        print(f"  keys         : {list(t.keys())}")
        print(f"  toolSpec keys: {list(t['toolSpec'].keys())}\n")

        print(f"Total base tools: {len(toolkit.as_anthropic_tools())}")


# ── 1. Google Gemini ──────────────────────────────────────────────────────────

async def google_demo() -> None:
    """
    Agent loop using the Google Generative AI SDK (Gemini API).

    as_google_tools() returns a single-item list containing a dict with a
    'function_declarations' key — the format Gemini's generate_content() expects.

    Works with both Gemini API (google.generativeai) and Vertex AI.

    Prerequisites:
        pip install llmbrowser[google]
        export GOOGLE_API_KEY=your_key
    """
    import google.generativeai as genai       # pip install llmbrowser[google]

    print("=== Google Gemini Agent Demo ===\n")

    async with LLMBrowser(headless=True) as browser:
        toolkit = BrowserToolkit(browser, state_mode="aria")
        tools = toolkit.as_google_tools()     # [{function_declarations: [...]}]

        model = genai.GenerativeModel(
            model_name="gemini-1.5-pro",
            tools=tools,
        )
        chat = model.start_chat(enable_automatic_function_calling=False)

        task = "Go to example.com and tell me the page title and any links you see."
        print(f"Task: {task}\n")
        response = chat.send_message(task)

        while True:
            # Collect all function calls in this response
            calls = [
                part.function_call
                for candidate in response.candidates
                for part in candidate.content.parts
                if part.function_call.name
            ]

            if not calls:
                # No tool calls — the model is done
                for candidate in response.candidates:
                    for part in candidate.content.parts:
                        if part.text:
                            print(f"Agent: {part.text}")
                break

            # Execute each tool call and collect results
            import google.generativeai.types as genai_types
            function_responses = []
            for call in calls:
                print(f"  [tool] {call.name}({dict(call.args)})")
                result = await toolkit.execute(call.name, **dict(call.args))
                function_responses.append(
                    genai_types.FunctionResponse(name=call.name, response={"result": result})
                )

            # Send results back to the model
            response = chat.send_message(
                [genai_types.Part(function_response=fr) for fr in function_responses]
            )


# ── 2. AWS Bedrock ────────────────────────────────────────────────────────────

async def bedrock_demo() -> None:
    """
    Agent loop using AWS Bedrock (Claude models via the Bedrock Runtime API).

    as_bedrock_tools() returns tools in the Bedrock toolSpec format:
        [{"toolSpec": {"name": ..., "description": ..., "inputSchema": {...}}}]

    Prerequisites:
        pip install llmbrowser[bedrock]
        aws configure   (or set AWS_REGION + credentials in environment)
    """
    import boto3                              # pip install llmbrowser[bedrock]
    import json

    print("=== AWS Bedrock Agent Demo ===\n")

    bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")

    async with LLMBrowser(headless=True) as browser:
        toolkit = BrowserToolkit(browser, state_mode="aria")
        tools = toolkit.as_bedrock_tools()    # [{toolSpec: {...}}]

        messages = [
            {
                "role": "user",
                "content": [
                    {"text": "Go to example.com and tell me the page title and any links you see."}
                ],
            }
        ]

        while True:
            response = bedrock.converse(
                modelId="anthropic.claude-sonnet-4-6-20250514-v1:0",
                messages=messages,
                toolConfig={"tools": tools},
            )

            output_message = response["output"]["message"]
            messages.append(output_message)

            stop_reason = response["stopReason"]

            if stop_reason == "end_turn":
                for block in output_message["content"]:
                    if "text" in block:
                        print(f"Agent: {block['text']}")
                break

            if stop_reason == "tool_use":
                tool_results = []
                for block in output_message["content"]:
                    if block.get("type") == "tool_use" or "toolUse" in block:
                        tool_block = block.get("toolUse", block)
                        name = tool_block["name"]
                        inputs = tool_block["input"]
                        print(f"  [tool] {name}({inputs})")
                        result = await toolkit.execute(name, **inputs)
                        tool_results.append({
                            "toolResult": {
                                "toolUseId": tool_block["toolUseId"],
                                "content": [{"text": result}],
                            }
                        })

                messages.append({"role": "user", "content": tool_results})


# ── 3. LangChain ──────────────────────────────────────────────────────────────

async def langchain_demo() -> None:
    """
    Use llmbrowser tools in a LangChain agent.

    as_langchain_tools() returns a list of LangChain StructuredTool objects.
    Each tool wraps an async function and has a fully-typed input schema.
    You can pass them directly to any LangChain agent executor.

    Prerequisites:
        pip install llmbrowser[langchain]
        pip install langchain-anthropic   (or langchain-openai, etc.)
        export ANTHROPIC_API_KEY=your_key
    """
    from langchain_anthropic import ChatAnthropic          # or ChatOpenAI, etc.
    from langchain.agents import AgentExecutor, create_tool_calling_agent
    from langchain_core.prompts import ChatPromptTemplate

    print("=== LangChain Agent Demo ===\n")

    async with LLMBrowser(headless=True) as browser:
        toolkit = BrowserToolkit(browser, state_mode="aria")
        tools = toolkit.as_langchain_tools()   # list[StructuredTool]

        print(f"  LangChain tools: {[t.name for t in tools[:4]]} ... ({len(tools)} total)\n")

        llm = ChatAnthropic(model="claude-sonnet-4-6")

        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are a web browsing agent. Use the browser tools to complete tasks."),
            ("human", "{input}"),
            ("placeholder", "{agent_scratchpad}"),
        ])

        agent = create_tool_calling_agent(llm, tools, prompt)
        executor = AgentExecutor(agent=agent, tools=tools, verbose=True)

        result = await executor.ainvoke({
            "input": "Go to example.com and tell me the page title and any links you see."
        })
        print(f"\nAgent: {result['output']}")


# ── 4. LlamaIndex ─────────────────────────────────────────────────────────────

async def llamaindex_demo() -> None:
    """
    Use llmbrowser tools in a LlamaIndex agent.

    as_llamaindex_tools() returns a list of LlamaIndex FunctionTool objects.
    Pass them directly to any LlamaIndex agent (ReActAgent, OpenAIAgent, etc.).

    Prerequisites:
        pip install llmbrowser[llamaindex]
        pip install llama-index-llms-anthropic   (or llms-openai, etc.)
        export ANTHROPIC_API_KEY=your_key
    """
    from llama_index.core.agent import ReActAgent
    from llama_index.llms.anthropic import Anthropic         # or llms.openai, etc.

    print("=== LlamaIndex Agent Demo ===\n")

    async with LLMBrowser(headless=True) as browser:
        toolkit = BrowserToolkit(browser, state_mode="aria")
        tools = toolkit.as_llamaindex_tools()  # list[FunctionTool]

        print(f"  LlamaIndex tools: {[t.metadata.name for t in tools[:4]]} ... ({len(tools)} total)\n")

        llm = Anthropic(model="claude-sonnet-4-6")
        agent = ReActAgent.from_tools(tools, llm=llm, verbose=True)

        response = await agent.achat(
            "Go to example.com and tell me the page title and any links you see."
        )
        print(f"\nAgent: {response}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--formats" in sys.argv:
        asyncio.run(show_formats_demo())
    elif "--google" in sys.argv:
        asyncio.run(google_demo())
    elif "--bedrock" in sys.argv:
        asyncio.run(bedrock_demo())
    elif "--langchain" in sys.argv:
        asyncio.run(langchain_demo())
    elif "--llamaindex" in sys.argv:
        asyncio.run(llamaindex_demo())
    else:
        # Default: show schema formats (no API key needed)
        asyncio.run(show_formats_demo())
        print("\nRun with --google / --bedrock / --langchain / --llamaindex for full demos.")
