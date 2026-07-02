---
name: skill-eval
description: >
  A/B evaluation pipeline for Claude Code skills. Use this skill whenever the
  user wants to evaluate, benchmark, compare, or score a skill — "evaluate this
  skill", "compare these two skills", "how much does this skill improve
  performance", "test my skill against a baseline prompt", or mentions
  skill-eval. Runs an interactive intake (purpose, requirements, test cases),
  classifies the skill into a category, co-creates independent test cases with
  the user via parallel generator agents, then calls the skill-eval CLI to
  execute, score, and report.
---

# skill-eval: evaluate a skill's real uplift

You orchestrate an evaluation pipeline. The interactive phases (0–2) happen in
this conversation; the execution/scoring phases (3–5) are delegated to the
`skill-eval` CLI at `~/dev/skill-eval` — never run test cases inside this chat,
the CLI runs them in fresh headless sessions for isolation.

CLI invocation (from any directory):

```bash
uv run --project ~/dev/skill-eval skill-eval <command> -c <eval.yaml>
```

## Phase 0 — Intake

Determine the mode from what the user provides:

- **Two skill.md files** → mode `skill-vs-skill`. Arm A = first skill, arm B = second.
- **One skill.md file** → mode `skill-vs-baseline`. Ask the user for the
  baseline prompt style they would use WITHOUT the skill (arm A = baseline,
  arm B = the skill).

Read the skill file(s), then ask the user (AskUserQuestion, or free text where
options don't fit):

1. What is the purpose of the skill? (propose a summary from the file, let them correct it)
2. What are the test requirements? (input format, success criteria, forbidden behaviors)
3. Do they have initial test cases? Collect any they provide.

Create a working directory `skill-eval-runs/<name>-<date>/` next to the skill
being tested, and start writing `eval.yaml` there (see
`~/dev/skill-eval/examples/eval.example.yaml` for the schema).

## Phase 1 — Classification

Run `uv run --project ~/dev/skill-eval skill-eval categories` to get the
category list. Pick the best-fitting category from purpose + requirements,
tell the user which one and why, and **confirm with them** before proceeding —
the category decides which metrics score the skill. Record it in eval.yaml.

## Phase 2 — Test case co-creation (loop until the user says stop)

Each round:

1. Spawn 3–4 generator subagents **in parallel, in one message**. Each agent
   gets ONLY: the purpose, the requirements, the category, one assigned lens,
   and a list of one-line "capability points already covered" (never full
   existing cases — this keeps generations independent). Lenses:
   - `happy-path`: typical, representative inputs
   - `edge`: empty/huge/malformed/boundary inputs
   - `adversarial`: inputs that tempt the skill to violate its requirements
   - `format-variance`: same intent, different phrasings and input formats
   Each agent returns 1–2 candidate cases as YAML matching the case schema
   (id, lens, prompt, expected where a ground truth exists).
2. Deduplicate: drop candidates whose capability point matches one already covered.
3. Present each surviving candidate to the user one at a time: "這個例子合理嗎?"
   with options accept / edit / reject. Append accepted cases to eval.yaml.
4. Ask whether to generate another round. **Only the user decides when to stop.**

Cases with deterministic ground truth (`expected`) are worth pushing for — a
case without one is scored by judge metrics only. Aim to tell the user when
coverage looks thin (e.g. no edge cases accepted yet), but never add cases
they didn't approve.

## Phase 3–5 — Execute, score, report (CLI)

```bash
uv run --project ~/dev/skill-eval skill-eval validate -c eval.yaml
uv run --project ~/dev/skill-eval skill-eval run      -c eval.yaml
uv run --project ~/dev/skill-eval skill-eval score    -c eval.yaml
uv run --project ~/dev/skill-eval skill-eval report   -c eval.yaml
```

Notes:
- `run` resumes: already-completed (case, arm, run) combinations are skipped,
  so a crashed run can just be re-invoked.
- `run` can take minutes (cases × 2 arms × runs_per_case headless sessions).
  Run it in the background and report progress.
- If cases need file access (fixtures, doc updates), set
  `permission_mode: bypassPermissions` in eval.yaml and warn the user first.

## Final report

Read `results/report.md` and give the user: the composite scores, the uplift
number, which metrics drove it, and — most importantly — the regression cases
where the skill did WORSE, with a one-line hypothesis each for why. If there
are fewer than 10 cases, remind them the result is directional.
