"""
llmbrowser benchmark harness
==============================

Runs a set of browser tasks using llmbrowser + AWS Bedrock Converse API,
then writes a results CSV with per-task metrics.

Usage
-----
    # Run all tasks (headless)
    uv run python benchmark/run.py

    # Run specific tasks
    uv run python benchmark/run.py --tasks wiki-001 duck-002 gh-001

    # Run a specific category
    uv run python benchmark/run.py --category search

    # Run with visible browser (useful for debugging)
    uv run python benchmark/run.py --no-headless --tasks wiki-001

    # Custom task file and output
    uv run python benchmark/run.py --tasks-file benchmark/tasks.json --output results.csv

    # Limit concurrency (default: 1 — sequential, safe for rate limits)
    uv run python benchmark/run.py --concurrency 3

    # Responsive / viewport-aware testing
    uv run python benchmark/run.py --viewports mobile
    uv run python benchmark/run.py --viewports mobile tablet desktop
    uv run python benchmark/run.py --viewports mobile desktop --tasks wiki-001 duck-001

Prerequisites
-------------
    pip install 'llmbrowser[bedrock]' boto3
    aws configure  # or set AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_DEFAULT_REGION
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import boto3

from llmbrowser import LLMBrowser
from llmbrowser.browser_tools import BrowserToolkit

# ── Config ────────────────────────────────────────────────────────────────────

MODEL_ID   = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
REGION     = "us-east-1"
MAX_TOKENS = 4096

# Viewport presets — covers the three standard responsive breakpoints.
VIEWPORTS: dict[str, dict] = {
    "mobile":  {"width": 375,  "height": 812},   # iPhone 14 Pro
    "tablet":  {"width": 768,  "height": 1024},  # iPad
    "desktop": {"width": 1280, "height": 800},   # standard laptop
}

SYSTEM_PROMPT = """\
You are a browser automation agent. You have access to a real web browser.

Complete the given task by using the browser tools. When you have the answer:
1. State the answer clearly.
2. Call the `finish` tool with your final answer string.

Rules:
- Always call `navigate` or `get_page_state` first to orient yourself.
- Be concise — avoid unnecessary tool calls.
- If a page doesn't load, try once more then report failure via `finish`.
- Never loop endlessly — if stuck after 3 attempts at the same step, call `finish` with what you have.
"""

TASKS_FILE    = Path(__file__).parent / "tasks.json"
DEFAULT_OUTPUT = Path(__file__).parent / "results.csv"

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("benchmark")

# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class Task:
    id:                str
    category:          str
    task:              str
    expected_contains: list[str]
    max_steps:         int


@dataclass
class TaskResult:
    task_id:       str
    category:      str
    task:          str
    viewport:      str           # "desktop", "mobile", "tablet", or "custom"
    success:       bool          # True = completed with an answer
    correct:       bool          # True = answer contains all expected strings
    answer:        str           # final answer returned by the agent
    steps:         int           # number of tool calls made
    input_tokens:  int
    output_tokens: int
    elapsed_s:     float
    error:         str = ""      # exception message if crashed


# ── Bedrock Converse agent loop ───────────────────────────────────────────────

def _make_finish_tool() -> dict:
    """A `finish` tool so the agent can signal task completion."""
    return {
        "toolSpec": {
            "name": "finish",
            "description": (
                "Call this when you have completed the task and have a final answer. "
                "Pass your complete answer as the `answer` parameter."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "answer": {
                            "type": "string",
                            "description": "Your complete final answer to the task.",
                        }
                    },
                    "required": ["answer"],
                }
            },
        }
    }


async def run_task(
    task:         Task,
    bedrock:      Any,
    headless:     bool = True,
    viewport_name: str = "desktop",
    viewport:     dict | None = None,
) -> TaskResult:
    """Run a single benchmark task at a given viewport. Returns a TaskResult."""
    t0 = time.perf_counter()
    steps         = 0
    input_tokens  = 0
    output_tokens = 0
    answer        = ""
    error         = ""
    finished      = False
    vp            = viewport or VIEWPORTS.get(viewport_name, VIEWPORTS["desktop"])

    try:
        async with LLMBrowser(
            headless=headless,
            stealth=False,
            browser="chromium",
            viewport=vp,
        ) as browser:
            toolkit = BrowserToolkit(browser)

            # Build tool list: all browser tools + finish signal
            bedrock_tools = toolkit.as_bedrock_tools() + [_make_finish_tool()]

            messages: list[dict] = [
                {"role": "user", "content": [{"text": task.task}]}
            ]

            while steps < task.max_steps and not finished:
                response = bedrock.converse(
                    modelId=MODEL_ID,
                    system=[{"text": SYSTEM_PROMPT}],
                    messages=messages,
                    toolConfig={"tools": bedrock_tools},
                    inferenceConfig={"maxTokens": MAX_TOKENS},
                )

                usage          = response.get("usage", {})
                input_tokens  += usage.get("inputTokens", 0)
                output_tokens += usage.get("outputTokens", 0)

                assistant_content = response["output"]["message"]["content"]
                messages.append({"role": "assistant", "content": assistant_content})

                stop_reason = response.get("stopReason", "")

                # Collect all tool calls in this response turn
                tool_calls = [b for b in assistant_content if "toolUse" in b]

                if not tool_calls:
                    # Model responded with text only — treat as a completion
                    text_blocks = [b["text"] for b in assistant_content if "text" in b]
                    answer = " ".join(text_blocks).strip()
                    finished = True
                    break

                # Execute each tool call and collect results
                tool_results = []
                for block in tool_calls:
                    tc       = block["toolUse"]
                    tool_id  = tc["toolUseId"]
                    name     = tc["name"]
                    inp      = tc.get("input", {})
                    steps   += 1

                    if name == "finish":
                        answer   = inp.get("answer", "")
                        finished = True
                        # Still need to send a tool result so Bedrock doesn't error
                        tool_results.append({
                            "toolResult": {
                                "toolUseId": tool_id,
                                "content":   [{"text": "Task marked as complete."}],
                            }
                        })
                        break  # don't execute remaining tools after finish

                    logger.info("[%s] step %d  →  %s(%s)", task.id, steps, name, inp)
                    try:
                        result_text = await toolkit.execute(name, **inp)
                        if not isinstance(result_text, str):
                            result_text = str(result_text)
                    except Exception as exc:
                        result_text = f"ERROR: {exc}"

                    tool_results.append({
                        "toolResult": {
                            "toolUseId": tool_id,
                            "content":   [{"text": result_text}],
                        }
                    })

                # Append tool results as a user turn.
                # If we're close to the step limit, add a warning so the agent
                # calls `finish` instead of continuing to browse.
                if tool_results:
                    steps_left = task.max_steps - steps
                    if steps_left <= 3 and not finished:
                        tool_results.append({"text": (
                            f"IMPORTANT: You have only {steps_left} tool call(s) remaining. "
                            "You MUST call `finish` with your best answer NOW. "
                            "Do not make any more browser calls."
                        )})
                    messages.append({"role": "user", "content": tool_results})

                if finished:
                    break

                if stop_reason == "end_turn" and not tool_calls:
                    break

    except Exception as exc:
        error  = str(exc)
        logger.error("[%s] CRASHED: %s", task.id, exc)

    elapsed = time.perf_counter() - t0

    # Score: correct if answer contains at least one of the expected strings (case-insensitive).
    # expected_contains lists acceptable correct values, not all required values.
    correct = bool(answer) and any(
        exp.lower() in answer.lower()
        for exp in task.expected_contains
    ) if task.expected_contains else bool(answer)

    return TaskResult(
        task_id       = task.id,
        category      = task.category,
        task          = task.task,
        success       = bool(answer) and not error,
        correct       = correct,
        viewport      = viewport_name,
        answer        = answer[:300],   # truncate for CSV readability
        steps         = steps,
        input_tokens  = input_tokens,
        output_tokens = output_tokens,
        elapsed_s     = round(elapsed, 2),
        error         = error[:200],
    )


# ── Runner ────────────────────────────────────────────────────────────────────

async def run_all(
    tasks:       list[Task],
    headless:    bool,
    concurrency: int,
    viewports:   list[str] | None = None,
) -> list[TaskResult]:
    """Run all tasks × viewports with bounded concurrency."""
    bedrock   = boto3.client("bedrock-runtime", region_name=REGION)
    sem       = asyncio.Semaphore(concurrency)
    vp_names  = viewports or ["desktop"]

    async def bounded(task: Task, vp_name: str) -> TaskResult:
        async with sem:
            label = f"[{task.id}@{vp_name}]"
            print(f"  → {label} {task.task[:65]}...")
            result = await run_task(task, bedrock, headless=headless, viewport_name=vp_name)
            status = "CORRECT" if result.correct else ("OK" if result.success else "FAIL")
            print(
                f"     {status:7}  steps={result.steps}  "
                f"tokens={result.input_tokens + result.output_tokens}  "
                f"time={result.elapsed_s}s"
            )
            if result.answer:
                print(f"     answer: {result.answer[:80]}")
            if result.error:
                print(f"     error:  {result.error[:80]}")
            return result

    coros = [bounded(t, vp) for t in tasks for vp in vp_names]
    results = await asyncio.gather(*coros)
    return list(results)


# ── Output ────────────────────────────────────────────────────────────────────

def write_csv(results: list[TaskResult], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "task_id", "category", "viewport", "success", "correct",
            "steps", "input_tokens", "output_tokens", "total_tokens",
            "elapsed_s", "answer", "task", "error",
        ])
        writer.writeheader()
        for r in results:
            writer.writerow({
                "task_id":       r.task_id,
                "category":      r.category,
                "viewport":      r.viewport,
                "success":       r.success,
                "correct":       r.correct,
                "steps":         r.steps,
                "input_tokens":  r.input_tokens,
                "output_tokens": r.output_tokens,
                "total_tokens":  r.input_tokens + r.output_tokens,
                "elapsed_s":     r.elapsed_s,
                "answer":        r.answer,
                "task":          r.task,
                "error":         r.error,
            })


def print_summary(results: list[TaskResult]) -> None:
    n          = len(results)
    n_success  = sum(1 for r in results if r.success)
    n_correct  = sum(1 for r in results if r.correct)
    total_tok  = sum(r.input_tokens + r.output_tokens for r in results)
    avg_steps  = sum(r.steps for r in results) / n if n else 0
    avg_time   = sum(r.elapsed_s for r in results) / n if n else 0

    print("\n" + "=" * 60)
    print("BENCHMARK SUMMARY")
    print("=" * 60)
    print(f"  Tasks run        : {n}")
    print(f"  Completed (any)  : {n_success}/{n}  ({100*n_success//n if n else 0}%)")
    print(f"  Correct answers  : {n_correct}/{n}  ({100*n_correct//n if n else 0}%)")
    print(f"  Avg steps/task   : {avg_steps:.1f}")
    print(f"  Avg time/task    : {avg_time:.1f}s")
    print(f"  Total tokens     : {total_tok:,}")
    print()

    # Per-category breakdown
    categories: dict[str, list[TaskResult]] = {}
    for r in results:
        categories.setdefault(r.category, []).append(r)

    print("  Category breakdown:")
    for cat, rs in sorted(categories.items()):
        nc = sum(1 for r in rs if r.correct)
        print(f"    {cat:12}  {nc}/{len(rs)} correct")

    # Per-viewport breakdown (only shown when multiple viewports were tested)
    viewports_seen = sorted({r.viewport for r in results})
    if len(viewports_seen) > 1:
        print()
        print("  Viewport breakdown:")
        for vp in viewports_seen:
            vp_rs = [r for r in results if r.viewport == vp]
            nc    = sum(1 for r in vp_rs if r.correct)
            print(f"    {vp:8}  {nc}/{len(vp_rs)} correct")

    print("=" * 60)


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="llmbrowser benchmark harness (AWS Bedrock Converse)"
    )
    p.add_argument(
        "--tasks",
        nargs="*",
        metavar="ID",
        help="Run only these task IDs (e.g. --tasks wiki-001 duck-002)",
    )
    p.add_argument(
        "--category",
        metavar="CAT",
        help="Run only tasks in this category (e.g. --category search)",
    )
    p.add_argument(
        "--tasks-file",
        default=str(TASKS_FILE),
        metavar="PATH",
        help=f"Path to tasks JSON file (default: {TASKS_FILE})",
    )
    p.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        metavar="PATH",
        help=f"Output CSV path (default: {DEFAULT_OUTPUT})",
    )
    p.add_argument(
        "--no-headless",
        action="store_true",
        help="Show the browser window (useful for debugging)",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=1,
        metavar="N",
        help="Number of tasks to run in parallel (default: 1)",
    )
    p.add_argument(
        "--model",
        default=MODEL_ID,
        metavar="MODEL_ID",
        help=f"Bedrock model ID (default: {MODEL_ID})",
    )
    p.add_argument(
        "--region",
        default=REGION,
        metavar="REGION",
        help=f"AWS region (default: {REGION})",
    )
    p.add_argument(
        "--viewports",
        nargs="+",
        metavar="VP",
        choices=["mobile", "tablet", "desktop"],
        help=(
            "Viewport(s) to test (default: desktop). "
            "Pass multiple to test each task at each size, e.g. --viewports mobile desktop"
        ),
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Show tool calls as they happen",
    )
    return p.parse_args()


async def main() -> int:
    args = parse_args()

    if args.verbose:
        logging.getLogger("benchmark").setLevel(logging.INFO)

    # Override globals from CLI
    global MODEL_ID, REGION
    MODEL_ID = args.model
    REGION   = args.region

    # Load tasks
    tasks_path = Path(args.tasks_file)
    if not tasks_path.exists():
        print(f"ERROR: tasks file not found: {tasks_path}", file=sys.stderr)
        return 1

    raw = json.loads(tasks_path.read_text())
    all_tasks = [Task(**t) for t in raw]

    # Filter
    if args.tasks:
        all_tasks = [t for t in all_tasks if t.id in args.tasks]
    if args.category:
        all_tasks = [t for t in all_tasks if t.category == args.category]

    if not all_tasks:
        print("No tasks matched the given filters.", file=sys.stderr)
        return 1

    viewports = args.viewports or ["desktop"]

    print(f"\nllmbrowser benchmark  —  {datetime.now():%Y-%m-%d %H:%M}")
    print(f"Model      : {MODEL_ID}")
    print(f"Region     : {REGION}")
    print(f"Tasks      : {len(all_tasks)}")
    print(f"Viewports  : {', '.join(viewports)}")
    print(f"Concurrency: {args.concurrency}")
    print(f"Headless   : {not args.no_headless}")
    print()

    results = await run_all(
        tasks       = all_tasks,
        headless    = not args.no_headless,
        concurrency = args.concurrency,
        viewports   = viewports,
    )

    output_path = Path(args.output)
    write_csv(results, output_path)
    print(f"\nResults written to: {output_path}")

    print_summary(results)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
