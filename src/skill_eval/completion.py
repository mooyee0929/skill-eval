"""Single-shot text completion over whichever backend the eval config uses:
executor.api (HTTP), executor.judge_cmd (custom CLI template), or the claude
CLI by default. Used by judges and by test-case generation — anything that
needs one prompt in, one text out, no tools."""

from __future__ import annotations

import json
import subprocess

from .api_client import ApiError, complete
from .config import Executor


class CompletionError(RuntimeError):
    pass


def complete_text(
    executor: Executor,
    prompt: str,
    *,
    model: str,
    timeout_s: int = 180,
) -> str:
    if executor.api is not None:
        try:
            text, _ = complete(
                executor.api, prompt=prompt, system=None, model=model, timeout_s=timeout_s
            )
        except ApiError as exc:
            raise CompletionError(str(exc)) from exc
        return text

    if executor.judge_cmd is not None:
        cmd = [
            part.replace("{model}", model).replace("{prompt}", prompt)
            for part in executor.judge_cmd
        ]
        output_format = executor.output_format
    else:
        cmd = ["claude", "-p", prompt, "--output-format", "json", "--model", model, "--max-turns", "1"]
        output_format = "claude-json"

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise CompletionError(f"completion timed out after {timeout_s}s") from exc
    if proc.returncode != 0:
        raise CompletionError(f"exit {proc.returncode}: {proc.stderr.strip()[:500]}")
    if output_format == "text":
        return proc.stdout
    try:
        return str(json.loads(proc.stdout).get("result", ""))
    except json.JSONDecodeError as exc:
        raise CompletionError(f"unparseable CLI output: {proc.stdout[:300]}") from exc
