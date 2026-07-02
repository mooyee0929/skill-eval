# skill-eval

A/B evaluation pipeline for Claude Code skills: measure how much a skill
actually improves performance, either **skill vs skill** or **skill vs
baseline prompt**.

## Architecture

- **Interactive layer** — a Claude Code skill (`skill/SKILL.md`, installed to
  `~/.claude/skills/skill-eval/`). Handles intake (purpose, requirements),
  category classification (confirmed with you), and multi-agent test-case
  co-creation where every candidate case is approved by you before it enters
  the suite.
- **Engine** — this Python CLI. Deterministic, reproducible, no chat involved:
  every (case, arm, run) executes in a fresh headless `claude -p` session in an
  isolated temp workspace, then gets scored and aggregated.

```
eval.yaml ──▶ skill-eval run ──▶ results/{case}/{arm}/run-N.json
              skill-eval score ──▶ results/scores.json
              skill-eval report ──▶ results/report.md
```

## Full pipeline walkthrough

The fastest path is a chat front-end that walks these steps with you —
`/skill-eval` in Claude Code, or `/skill-eval` in VS Code Copilot Chat
(agent mode) with this repo's prompt file. Everything below is the same
pipeline driven by hand.

### Step 0 — Install (pick one)

```bash
# a) from this repo
uv sync && uv run skill-eval --help

# b) prebuilt wheel
uv tool install https://github.com/mooyee0929/skill-eval/releases/latest/download/skill_eval-0.5.0-py3-none-any.whl

# c) no uv/pip: single-file zipapp, only needs Python 3.8+
curl -LO https://github.com/mooyee0929/skill-eval/releases/latest/download/skill-eval.pyz
alias skill-eval="python3 $PWD/skill-eval.pyz"
```

Runs and judges need a model backend: the `claude` CLI on PATH (default),
any other agent CLI, or a raw HTTP API — see the executor sections below.

### Step 1 — Pick the category

```bash
skill-eval categories        # lists every category and its metrics/weights
```

Choose the category that matches what the skill does; it decides which
metrics score the evaluation (e.g. `diagram_generation` → render success,
edge F1, readability).

### Step 2 — Write eval.yaml

```bash
mkdir my-eval && cd my-eval
```

Minimal config (full schema with every option: `examples/eval.example.yaml`):

```yaml
mode: skill-vs-baseline          # or: skill-vs-skill
purpose: >
  One paragraph: what the skill is supposed to do well.
requirements:
  - Success criteria and forbidden behaviors, one per line
category: diagram_generation     # from step 1

arms:
  - label: baseline              # no `skill:` key = bare model
  - label: my skill
    skill: ../my-skill/SKILL.md  # skill-vs-skill: give both arms a skill

runs_per_case: 3
model: claude-sonnet-4-6
judge_model: claude-haiku-4-5-20251001

cases: []                        # filled in step 3
```

### Step 3 — Generate test cases, approve, merge (repeat until satisfied)

```bash
skill-eval gen-cases -c eval.yaml --per-lens 2
```

One independent generator per lens (happy-path / edge / adversarial /
format-variance) runs in parallel and writes deduped candidates to
`candidates.yaml`. Open it, review each candidate (edit freely), then merge
only the ones you accept:

```bash
skill-eval add-case -c eval.yaml --from candidates.yaml --ids happy-x,edge-y
# or --all if every candidate passed review
```

Re-run `gen-cases` for another round — merged cases' capability points are
automatically excluded from the next generation. Aim for 10+ cases with a
deterministic `expected` block; fewer works but the report will flag the
result as directional.

### Step 4 — Sanity-check the config

```bash
skill-eval validate -c eval.yaml
```

Flags unknown categories, missing skill files, duplicate case ids, and
tells you which cases have no deterministic ground truth (judge-only).

### Step 5 — Execute

```bash
skill-eval run -c eval.yaml
```

Every (case × arm × run) executes in a fresh headless session in an
isolated temp workspace: `cases × 2 arms × runs_per_case` sessions total,
so expect minutes, not seconds. Interrupted? Just re-run — completed
combinations are cached in `results/` and skipped.

### Step 6 — Score

```bash
skill-eval score -c eval.yaml
```

Deterministic metrics are computed in-process; judge metrics fan out to a
parallel pool of blind LLM votes. Output: `results/scores.json`.

### Step 7 — Report

```bash
skill-eval report -c eval.yaml     # prints and saves results/report.md
```

Read it in this order: composite score per arm → uplift (arm B − arm A) →
per-metric deltas (what drove the difference) → **Regressions** (cases where
arm B lost — read those runs' JSON in `results/<case>/<arm>/` first, they
are where the real insight lives).

Config schema: see `examples/eval.example.yaml`. Category → metric mapping:
`src/skill_eval/taxonomy.yaml`.

## Scoring model

- **Deterministic metrics** (exact match, field-level F1, precision@k, MRR,
  hit@k, ROUGE-L, edge F1, render success, broken-link rate, update success)
  are computed in code from the run output vs the case's `expected` block —
  see `src/skill_eval/scorers/deterministic.py`.
- **Judge metrics** (factual consistency, readability, template coverage, …)
  use a blind LLM judge: it sees the task prompt, the rubric, and one anonymous
  response — never which arm produced it. Each score is the mean of N
  independent votes (default 3), normalized to [0, 1].
- **Meta metrics**: latency and failure rate from run telemetry. A failed run
  scores 0 on all quality metrics.
- **Aggregation**: per-metric means per arm, normalized so higher = better
  (lower-is-better rates are inverted; latency is fastest/mean), then weighted
  by the category's weights into a composite. The report shows composite,
  uplift (arm B − arm A), per-metric deltas, and the regression cases where
  arm B underperformed.

## Independence guarantees

- Test-case generators run as parallel agents that see only purpose,
  requirements, lens, and a list of already-covered capability *points* —
  never each other's outputs or full existing cases.
- Every run is a fresh session in a fresh temp directory; fixtures are copied
  per run, so runs cannot contaminate each other.
- Judges are blind to arm identity and vote independently.

## Running on other agent CLIs

By default runs and judges execute through the `claude` CLI. Set an
`executor` block in eval.yaml to use any CLI that accepts a prompt and
prints a response:

```yaml
executor:
  run_cmd: ["gemini", "-m", "{model}", "-p", "{prompt}"]
  judge_cmd: ["gemini", "-m", "{model}", "-p", "{prompt}"]
  output_format: text
```

`{skill}` marks where the skill content goes (e.g. claude's
`--append-system-prompt {skill}`); without a `{skill}` entry the skill is
prepended to the prompt, which works on every CLI.

## Using it from GitHub Copilot Chat (VS Code)

The interactive front-end doesn't have to be Claude Code. This repo ships
`.github/prompts/skill-eval.prompt.md` — open the repo in VS Code, type
`/skill-eval` in Copilot Chat (agent mode), and Copilot walks the same
protocol: intake → category confirmation → case approval → CLI execution.
Copy the file into any project's `.github/prompts/` to use it there.

Test-case generation stays independent regardless of front-end, because it
lives in the engine:

```bash
skill-eval gen-cases -c eval.yaml --per-lens 2     # parallel independent generators
# review candidates.yaml, then merge only the approved ids:
skill-eval add-case  -c eval.yaml --from candidates.yaml --ids happy-one,edge-two
```

## No agent CLI? Use the HTTP API executor

If the machine has no agent CLI at all, point the executor at an HTTP
endpoint instead — the Anthropic Messages API or any OpenAI-compatible
gateway (Ollama, vLLM, internal proxies). stdlib-only, no extra deps:

```yaml
executor:
  api:
    kind: anthropic            # or: openai
    api_key_env: ANTHROPIC_API_KEY
    # base_url: http://gw.internal:8080   # for gateways / local models
```

API mode is single-shot text generation: skills inject as the system
prompt, but there are no tools or file access, so `check_cmd` cases are
skipped. You can also run `run` on a machine that has a CLI, copy
`results/` over, and do `score`/`report` elsewhere.

## No uv/pip? Use the zipapp

Each release ships `skill-eval.pyz`, a self-contained zipapp with all
dependencies bundled. It needs only Python 3.8+:

```bash
python3 skill-eval.pyz categories
python3 skill-eval.pyz run -c eval.yaml
```

## Tests

```bash
uv run pytest
```
