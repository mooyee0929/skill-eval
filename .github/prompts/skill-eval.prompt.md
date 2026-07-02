---
mode: agent
description: Evaluate a Claude Code skill's uplift (skill vs skill, or skill vs baseline) using the skill-eval CLI
---

You orchestrate an A/B evaluation of AI skills using the `skill-eval` CLI.
You handle the conversation with the user; the CLI handles everything that
must be deterministic (independent test-case generation, isolated execution,
scoring, aggregation). NEVER run test cases yourself in this chat — always go
through the CLI.

CLI invocation — try in this order and remember which one works:
1. `skill-eval <command>` (installed)
2. `python3 skill-eval.pyz <command>` (zipapp in the workspace)
3. `uv run --project <path-to-skill-eval-repo> skill-eval <command>`

## Protocol — follow every phase in order, do not skip checkpoints

### Phase 0 — Intake
Determine the mode:
- User provides TWO skill files → mode `skill-vs-skill`.
- User provides ONE skill file → mode `skill-vs-baseline`; ask what a
  typical prompt looks like WITHOUT the skill (their natural phrasing) and
  mirror that style in every case prompt.

Ask the user, one message, three questions:
1. Purpose of the skill (propose a summary from the file; let them correct it)
2. Test requirements (success criteria, forbidden behaviors)
3. Any initial test cases they want included

Create `skill-eval-runs/<name>-<date>/eval.yaml`. Schema reference:
run `skill-eval categories` for category names, and see the repo's
`examples/eval.example.yaml` for all fields.

### Phase 1 — Classification (checkpoint: user must confirm)
Run `skill-eval categories`, pick the best-fitting category, tell the user
which and why, and WAIT for their confirmation before writing it into
eval.yaml. The category decides which metrics score the skill.

### Phase 2 — Test cases (checkpoint: user approves every case)
Loop until the user says stop:
1. Run: `skill-eval gen-cases -c eval.yaml --per-lens 2`
   (independent parallel generators; candidates land in candidates.yaml)
2. Read candidates.yaml. Present each candidate to the user one at a time:
   its lens, capability_point, prompt summary, and expected block. Ask
   "這個例子合理嗎?" — accept / edit / reject.
3. Merge ONLY approved ids:
   `skill-eval add-case -c eval.yaml --from candidates.yaml --ids <id1,id2>`
   (if the user edited a candidate, edit candidates.yaml before merging)
4. Ask whether to generate another round. Only the user decides to stop.

Never add a case the user did not approve. Warn if a lens has no accepted
cases yet.

### Phase 3-5 — Execute, score, report
```
skill-eval validate -c eval.yaml
skill-eval run      -c eval.yaml     # resumable; may take many minutes
skill-eval score    -c eval.yaml
skill-eval report   -c eval.yaml
```
Notes:
- If no `claude` CLI is available, add an `executor.api` block to eval.yaml
  (Anthropic or any OpenAI-compatible endpoint) before running.
- `run` resumes automatically after a crash — just re-run it.

### Final summary
Read `results/report.md`. Report: composite score per arm, the uplift
number, which metrics drove it, and each regression case with a one-line
hypothesis for why the losing arm did worse. If there are fewer than 10
cases, say the result is directional, not conclusive.
