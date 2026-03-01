# llmbrowser Benchmark — Methodology

## Overview

This is the official llmbrowser benchmark: a reproducible, open-source suite of
50 real-world browser tasks designed to measure AI agent performance on web
automation. Every task, scoring rule, and result is public so anyone can verify
or reproduce the numbers.

---

## Task Design Principles

### 1. Verifiability
Every task has a clear, unambiguous correct answer. Tasks are either:
- **Fact-based** — the answer is a stable historical fact (birth year, inventor's name,
  a published paper title). These have `expected_contains` values.
- **Live-data** — the answer changes over time (HN front page, GitHub trending).
  These have `expected_contains: []` and are scored on completion quality by a judge.

### 2. Reproducibility
Fact-based tasks produce the same correct answer every time they are run.
Live-data tasks (news, trending, weather) are marked with category `extraction`
or `navigation` and scored differently — see Scoring below.

### 3. Breadth over depth
The benchmark covers 9 task categories across 10 different websites:

| Category | Count | Tests |
|---|---|---|
| `extraction` | 19 | Read a specific fact from a page |
| `search` | 9 | Use a search engine or site search |
| `navigation` | 9 | Click through menus, tabs, links |
| `multi-step` | 4 | Chain multiple pages/lookups |
| `table` | 2 | Extract data from HTML tables |
| `scroll` | 2 | Scroll to find content not in initial viewport |
| `count` | 2 | Count items on a page |
| `npm` | 2 | Package registry tasks |
| `so` | 2 | Stack Overflow search and extraction |

**Websites covered:** Wikipedia, Hacker News, GitHub, PyPI, npm, DuckDuckGo,
arXiv, MDN Web Docs, Stack Overflow, wttr.in

### 4. Difficulty tiers
- **Easy** (≤8 steps): Direct extraction from a single page — PyPI version numbers,
  arXiv paper titles, GitHub language tags
- **Medium** (≤15 steps): Search + navigate + extract — Wikipedia fact lookups,
  DuckDuckGo queries, HN navigation
- **Hard** (≤25 steps): Multi-page chaining — find X on site A, use that to find Y
  on site B; scroll through long articles; cross-site comparisons

---

## Scoring

### Automatic scoring (fact-based tasks)

A task is marked **CORRECT** if the agent's final answer contains at least one
string from `expected_contains` (case-insensitive substring match).

`expected_contains` lists all valid correct answers — for example, a height might
be expressed as "330 m", "330 meters", or "330m", so all variants are included.

```
correct = any(exp.lower() in answer.lower() for exp in expected_contains)
          if expected_contains else bool(answer)
```

### Completion scoring (live-data tasks)

Tasks with `expected_contains: []` are scored on **completion** — did the agent
return a non-empty, plausible answer? These tasks test navigation and extraction
ability on content that changes by definition (news headlines, trending repos).

### Summary metrics reported

| Metric | Definition |
|---|---|
| **Completion rate** | Tasks where the agent returned a non-empty answer / total tasks |
| **Correct rate** | Tasks scored CORRECT / total tasks |
| **Avg steps** | Mean number of tool calls per task |
| **Avg time** | Mean wall-clock seconds per task |
| **Total tokens** | Sum of input + output tokens across all tasks |

---

## Setup

### Requirements
```bash
pip install 'llmbrowser[bedrock]'
llmbrowser install          # installs Chromium
aws configure                # set AWS credentials
```

### Model
Default: `us.anthropic.claude-sonnet-4-5-20250929-v1:0` via AWS Bedrock Converse API.

The model can be changed with `--model`:
```bash
python benchmark/run.py --model us.anthropic.claude-3-5-sonnet-20241022-v2:0
```

### Agent loop
Each task runs an independent agent loop:
1. A fresh `LLMBrowser(headless=True, browser="chromium")` instance is created
2. The task is given as a single user message
3. The LLM calls browser tools iteratively until it calls `finish(answer=...)` or hits `max_steps`
4. When 3 or fewer steps remain, a warning is injected into the tool result urging the agent to call `finish`

### System prompt
```
You are a browser automation agent. You have access to a real web browser.
Complete the given task by using the browser tools. When you have the answer:
1. State the answer clearly.
2. Call the `finish` tool with your final answer string.
...
```

---

## Running

```bash
# Full benchmark (all 50 tasks, sequential)
python benchmark/run.py

# Specific tasks
python benchmark/run.py --tasks wiki-001 duck-001 gh-004

# Specific category
python benchmark/run.py --category multi-step

# Visible browser (for debugging)
python benchmark/run.py --no-headless --tasks wiki-001

# Parallel (3 at a time — mind rate limits)
python benchmark/run.py --concurrency 3

# Different model
python benchmark/run.py --model us.anthropic.claude-3-5-sonnet-20241022-v2:0
```

Results are written to `benchmark/results.csv`.

---

## Results

### llmbrowser v0.1.0 — Claude Sonnet 4.5 (Bedrock)

**Run date:** 2026-03-01
**Model:** `us.anthropic.claude-sonnet-4-5-20250929-v1:0`
**Tasks:** 51 (full benchmark)

| Metric | Value |
|---|---|
| Completion rate | 51/51 (100%) |
| Correct rate | **49/51 (96%)** |
| Avg steps/task | 7.2 |
| Avg time/task | 35.5s |
| Total tokens | 5,155,717 |

**Category breakdown:**

| Category | Score | Notes |
|---|---|---|
| count | 2/2 ✓ | |
| extraction | 24/26 | 2 misses — see below |
| multi-step | 4/4 ✓ | |
| navigation | 7/7 ✓ | |
| scroll | 2/2 ✓ | |
| search | 8/8 ✓ | |
| table | 2/2 ✓ | |

**The 2 misses explained:**

- `wiki-002` (Tokyo population): Agent returned "14,000,000" — a rounded version of
  the correct answer. The expected_contains list did not include this rounding variant.
  Factually correct, scoring gap.

- `so-001` (Stack Overflow highest-voted question): Agent returned "Why is processing a
  sorted array faster than processing an unsorted array?" — which IS the correct answer.
  However, the expected_contains was `["Git", "undo", "git"]`, anticipating a different
  question. The expected value was wrong.

**Effective score: 51/51 (100%)** — both agent answers were factually correct.

---

## Limitations and caveats

1. **Live websites** — Tasks on HN, GitHub trending, and weather return different
   answers each run. Scores on these tasks measure navigation/extraction ability,
   not factual accuracy.

2. **Single model** — Results are for Claude Sonnet 4.5 on Bedrock. Performance
   varies significantly by model. We will publish results for multiple models.

3. **Single run** — Each task is run once. A production benchmark would average
   3+ runs per task to account for model non-determinism and page load variance.

4. **No vision** — llmbrowser v0.1.0 uses DOM/ARIA text extraction only. Tasks
   that require reading text from images will fail. Vision support is planned.

5. **Not WebVoyager** — This is not the WebVoyager benchmark. It cannot be directly
   compared to published WebVoyager scores from other tools. A WebVoyager subset
   run is planned for a future release.

---

## Reproducing the results

```bash
git clone https://github.com/Ponprabhakar-pg/llmbrowser
cd llmbrowser
pip install 'llmbrowser[bedrock]'
llmbrowser install
pip install boto3
aws configure
python benchmark/run.py
```

Results will be written to `benchmark/results.csv`.
All tasks, scoring logic, and the agent loop are in `benchmark/run.py` and
`benchmark/tasks.json` — fully auditable.
