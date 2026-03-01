"""
llmbrowser — CAPTCHA solver hook & Human-in-the-Loop (HITL)
=============================================================

Both features follow the same pattern: you provide an async callback, and
llmbrowser calls it at the right moment. Your callback does the heavy lifting
(call a solver API, pause and wait for a human) and returns a result string.

CAPTCHA hook
------------
Register an async callback on LLMBrowser via on_captcha_detected=.
When the browser detects a CAPTCHA (reCAPTCHA v2/v3 or hCaptcha), it calls
your callback with a CaptchaContext object and injects the returned token
automatically. The agent never needs to know a CAPTCHA was present.

Human-in-the-Loop (HITL)
-------------------------
Register an async callback on BrowserToolkit via on_human_needed=.
This adds a request_human_help tool to the agent's toolbox. When the agent
hits something it can't handle (MFA code, CAPTCHA it can't solve, ambiguous
instruction), it calls the tool with an explanation. Your callback receives
a HumanHelpContext with the reason, URL, page title, and a screenshot, then
returns the human's response back to the agent.

Usage
-----
    python examples/captcha_and_hitl.py
    python examples/captcha_and_hitl.py --anthropic   # full agent loop
"""

import asyncio
import sys
import base64

from llmbrowser import LLMBrowser, BrowserToolkit, CaptchaContext, HumanHelpContext


# ── 1. CAPTCHA detection (manual) ────────────────────────────────────────────

async def captcha_detection_demo() -> None:
    """
    Manually detect and inspect what CAPTCHA is on a page.

    detect_captcha() returns None if no CAPTCHA is found, or a CaptchaContext
    with the CAPTCHA type and sitekey — the two values every solver API needs.

    Supported CAPTCHA types: "recaptcha_v2", "recaptcha_v3", "hcaptcha"
    """
    print("=== CAPTCHA Detection Demo ===\n")

    async with LLMBrowser(headless=True) as browser:
        # Load a page with a reCAPTCHA v2 widget
        await browser._page.set_content("""<!DOCTYPE html><html><body>
            <div class="g-recaptcha"
                 data-sitekey="6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI"
                 data-callback="onSolved"></div>
        </body></html>""")

        ctx = await browser.detect_captcha()

        if ctx is None:
            print("No CAPTCHA detected.")
        else:
            print(f"CAPTCHA detected!")
            print(f"  type    : {ctx.type}")
            print(f"  sitekey : {ctx.sitekey}")
            print(f"  url     : {ctx.url}")
            print()
            print("Pass ctx.sitekey and ctx.url to any solver API")
            print("(CapSolver, 2captcha, Anti-Captcha, etc.) to get a token.")


# ── 2. CAPTCHA solver hook ────────────────────────────────────────────────────

async def captcha_hook_demo() -> None:
    """
    Register a solver callback so CAPTCHA solving is fully automatic.

    How it works:
    1. After every navigate(), llmbrowser calls detect_captcha().
    2. If a CAPTCHA is found, it calls your on_captcha_detected callback.
    3. Your callback calls an external solver API and returns the token string.
    4. llmbrowser injects the token into the page automatically.
    5. The agent (and your code) never has to handle CAPTCHAs explicitly.

    The callback receives a CaptchaContext with:
        ctx.type    — "recaptcha_v2" | "recaptcha_v3" | "hcaptcha"
        ctx.sitekey — public sitekey to pass to the solver
        ctx.url     — current page URL (also required by most solver APIs)
    """
    print("=== CAPTCHA Solver Hook Demo ===\n")

    async def my_captcha_solver(ctx: CaptchaContext) -> str:
        """
        Replace this stub with a real solver call, e.g.:

            import capsolver
            capsolver.api_key = "YOUR_API_KEY"
            if ctx.type == "recaptcha_v2":
                solution = capsolver.solve({
                    "type": "ReCaptchaV2Task",
                    "websiteURL": ctx.url,
                    "websiteKey": ctx.sitekey,
                })
                return solution["gRecaptchaResponse"]

        For this demo we return a fake token to show the flow works.
        """
        print(f"  [solver] called for {ctx.type} on {ctx.url}")
        print(f"  [solver] sitekey = {ctx.sitekey}")
        # In production: call your solver API here and return the real token
        return "DEMO_FAKE_TOKEN_replace_with_real_solver"

    # Pass the callback to LLMBrowser — it runs automatically after each navigate
    async with LLMBrowser(headless=True, on_captcha_detected=my_captcha_solver) as browser:
        # Load a page with a reCAPTCHA widget
        await browser._page.set_content("""<!DOCTYPE html><html><body>
            <div class="g-recaptcha"
                 data-sitekey="6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI"
                 data-callback="onSolved"></div>
            <textarea id="g-recaptcha-response" style="display:none"></textarea>
        </body></html>""")

        # Manually trigger solve_captcha() to demonstrate injection
        # (normally called automatically by navigate())
        solved = await browser.solve_captcha()
        print(f"\n  solve_captcha() returned: {solved}")

        if solved:
            # Verify the token was injected into the hidden textarea
            injected = await browser._page.eval_on_selector(
                "#g-recaptcha-response", "el => el.value"
            )
            print(f"  Token in textarea     : {injected!r}")


# ── 3. Human-in-the-Loop (HITL) ───────────────────────────────────────────────

async def hitl_demo() -> None:
    """
    Add a human-intervention tool to the agent's toolbox.

    When the agent calls request_human_help, your on_human_needed callback is
    invoked with a HumanHelpContext containing:
        ctx.reason          — agent's explanation of why it needs help
        ctx.url             — current page URL
        ctx.title           — current page title
        ctx.screenshot_b64  — base64 JPEG of what the agent currently sees
        ctx.session_id      — stable ID for this toolkit session

    Your callback can:
    - Print the screenshot, show it in a UI, or send it to Slack/email
    - Wait for a human to type a response
    - Use a webhook (pause the agent, resume with the response later)
    - Return the human's input as a string — the agent continues with it

    The tool is only added to the agent's toolbox when on_human_needed is set,
    so agents without the callback never see or call the tool.
    """
    print("=== HITL Demo ===\n")

    async def my_human_handler(ctx: HumanHelpContext) -> str:
        """
        In production this would pause execution and wait for a real human.
        Common patterns:
          - CLI:     input("Human, please help: ")
          - Slack:   post message + poll for reply
          - Webhook: raise an event, resume when POST /resume is called

        For this demo we just print the context and return a canned response.
        """
        print(f"  [human needed] {ctx.reason}")
        print(f"  [human needed] page: {ctx.title} @ {ctx.url}")
        print(f"  [human needed] screenshot size: {len(ctx.screenshot_b64)} chars (base64 JPEG)")

        # Optionally save the screenshot so the human can view it:
        # with open("/tmp/agent_screenshot.jpg", "wb") as f:
        #     f.write(base64.b64decode(ctx.screenshot_b64))

        # In production: return the human's actual response
        return "The MFA code is 847291"

    async with LLMBrowser(headless=True) as browser:
        await browser.navigate("https://example.com")

        # on_human_needed wires up the request_human_help tool
        toolkit = BrowserToolkit(browser, on_human_needed=my_human_handler)

        # Verify the tool is present
        tool_names = {t["name"] for t in toolkit.as_anthropic_tools()}
        print(f"  Tools available: {len(tool_names)} total")
        print(f"  request_human_help present: {'request_human_help' in tool_names}\n")

        # Simulate the agent calling the tool
        response = await toolkit.execute(
            "request_human_help",
            reason="The page is asking for a two-factor authentication code and I cannot proceed.",
        )
        print(f"\n  Agent received from human: {response!r}")


# ── 4. Full Anthropic agent loop with both hooks ──────────────────────────────

async def anthropic_with_hooks_demo() -> None:
    """
    Anthropic agent loop with CAPTCHA solver + HITL both active.

    With both callbacks set, the toolkit exposes 18 tools:
    16 base tools + solve_captcha + request_human_help.
    The agent decides when to call each one.
    """
    import anthropic                          # pip install llmbrowser[anthropic]

    print("=== Anthropic Agent + CAPTCHA + HITL Demo ===\n")

    # ── Callbacks ──────────────────────────────────────────────────────────────

    async def captcha_solver(ctx: CaptchaContext) -> str:
        print(f"  [captcha] solving {ctx.type} on {ctx.url}")
        # Replace with your real solver:
        # return await call_capsolver_api(ctx.sitekey, ctx.url, ctx.type)
        return "PLACEHOLDER_TOKEN"

    async def human_handler(ctx: HumanHelpContext) -> str:
        print(f"\n  [HUMAN NEEDED] {ctx.reason}")
        # In production: await webhook / Slack message / CLI input
        return input("  Your response: ")

    # ── Browser + agent loop ───────────────────────────────────────────────────

    client = anthropic.Anthropic()
    messages = [
        {"role": "user", "content": "Go to example.com and summarise the page."},
    ]

    async with LLMBrowser(
        headless=True,
        on_captcha_detected=captcha_solver,   # auto-solves CAPTCHAs
    ) as browser:
        toolkit = BrowserToolkit(
            browser,
            state_mode="aria",
            on_human_needed=human_handler,    # adds request_human_help tool
        )

        print(f"  Total tools: {len(toolkit.as_anthropic_tools())}  (should be 18)\n")
        tools = toolkit.as_anthropic_tools()

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
                        print(f"\nAgent: {block.text}")
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


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--anthropic" in sys.argv:
        asyncio.run(anthropic_with_hooks_demo())
    else:
        asyncio.run(captcha_detection_demo())
        print()
        asyncio.run(captcha_hook_demo())
        print()
        asyncio.run(hitl_demo())
