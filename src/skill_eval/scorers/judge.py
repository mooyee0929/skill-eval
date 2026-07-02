"""Blind LLM judging via headless `claude -p`. The judge sees only the task
prompt, the rubric, and one anonymous response — never which arm produced it
— and each score is the mean of independent votes."""

from __future__ import annotations

import re

from ..completion import CompletionError, complete_text
from ..config import Executor, MetricDef

_SCORE_RE = re.compile(r'"score"\s*:\s*([1-5])')

_JUDGE_PROMPT = """You are a strict evaluation judge. Score one anonymous \
response against the rubric. You do not know how the response was produced; \
judge only what is on the page.

## Task given to the system under test
{task_prompt}

## Rubric ({metric_name})
{rubric}

## Response to score
<response>
{response}
</response>

Reply with ONLY a JSON object: {{"score": <integer 1-5>, "reason": "<one sentence>"}}"""


class JudgeError(RuntimeError):
    pass


def _ask_judge(
    prompt: str,
    model: str,
    executor: Executor,
    timeout_s: int = 120,
) -> int:
    try:
        result = complete_text(executor, prompt, model=model, timeout_s=timeout_s)
    except CompletionError as exc:
        raise JudgeError(str(exc)) from exc
    m = _SCORE_RE.search(result)
    if m is None:
        raise JudgeError(f"no score in judge reply: {result[:300]}")
    return int(m.group(1))


def judge_score(
    metric: MetricDef,
    task_prompt: str,
    response: str,
    *,
    model: str,
    votes: int = 3,
    executor: Executor | None = None,
) -> float:
    """Mean of `votes` independent 1-5 judgments, normalized to [0, 1]."""
    if metric.rubric is None:
        raise ValueError(f"metric '{metric.name}' has no rubric")
    prompt = _JUDGE_PROMPT.format(
        task_prompt=task_prompt,
        metric_name=metric.name,
        rubric=metric.rubric,
        response=response[:20000],
    )
    if executor is None:
        executor = Executor()
    scores: list[int] = []
    errors: list[str] = []
    for _ in range(votes):
        try:
            scores.append(_ask_judge(prompt, model, executor))
        except JudgeError as exc:
            errors.append(str(exc))
    if not scores:
        raise JudgeError(f"all {votes} judge votes failed: {errors}")
    return (sum(scores) / len(scores) - 1) / 4  # 1-5 -> 0-1
