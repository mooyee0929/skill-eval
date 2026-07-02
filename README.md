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

## Usage

```bash
uv sync                                    # once
uv run skill-eval categories               # taxonomy: categories × metrics
uv run skill-eval validate -c eval.yaml
uv run skill-eval run      -c eval.yaml    # resumable
uv run skill-eval score    -c eval.yaml
uv run skill-eval report   -c eval.yaml
```

Config schema: see `examples/eval.example.yaml`. Category → metric mapping:
`taxonomy.yaml`.

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

## Tests

```bash
uv run pytest
```
