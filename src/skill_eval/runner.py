"""Headless execution: each (case, arm, run) gets a fresh `claude -p` session
in an isolated workspace, so runs cannot contaminate each other."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import Arm, EvalConfig, TestCase


@dataclass(frozen=True)
class RunResult:
    case_id: str
    arm: str
    run_index: int
    ok: bool
    output: str
    latency_s: float
    error: str | None = None
    check_cmd_ok: bool | None = None  # result of the case's check_cmd, if any
    usage: dict[str, int] | None = None


def render_cmd_template(
    template: list[str],
    *,
    prompt: str,
    model: str,
    max_turns: int,
    skill_text: str | None,
) -> list[str]:
    """Render an executor command template. Entries containing {skill} (and an
    immediately preceding flag entry) are dropped when the arm has no skill.
    If no entry carries {skill} but the arm has one, the skill content is
    prepended to the prompt — the generic fallback for CLIs without a
    system-prompt flag."""
    has_skill_slot = any("{skill}" in part for part in template)
    effective_prompt = prompt
    if skill_text is not None and not has_skill_slot:
        effective_prompt = f"{skill_text}\n\n---\n\n{prompt}"

    cmd: list[str] = []
    for part in template:
        if "{skill}" in part:
            if skill_text is None:
                if cmd and cmd[-1].startswith("-"):
                    cmd.pop()
                continue
            part = part.replace("{skill}", skill_text)
        part = part.replace("{model}", model).replace("{max_turns}", str(max_turns))
        part = part.replace("{prompt}", effective_prompt)
        cmd.append(part)
    return cmd


def _build_cmd(cfg: EvalConfig, arm: Arm, prompt: str) -> list[str]:
    skill_text = (
        arm.skill_path.read_text(encoding="utf-8") if arm.skill_path is not None else None
    )
    if cfg.executor.run_cmd is not None:
        return render_cmd_template(
            cfg.executor.run_cmd,
            prompt=prompt,
            model=cfg.model,
            max_turns=cfg.max_turns,
            skill_text=skill_text,
        )
    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "json",
        "--model",
        cfg.model,
        "--max-turns",
        str(cfg.max_turns),
    ]
    if skill_text is not None:
        cmd += ["--append-system-prompt", skill_text]
    if cfg.permission_mode:
        cmd += ["--permission-mode", cfg.permission_mode]
    return cmd


def _parse_output(stdout: str, output_format: str) -> tuple[str, dict[str, int] | None]:
    if output_format == "text":
        return stdout.strip(), None
    payload = json.loads(stdout)
    usage_raw = payload.get("usage") or {}
    usage = {k: v for k, v in usage_raw.items() if isinstance(v, int)} or None
    return str(payload.get("result", "")), usage


def run_once(cfg: EvalConfig, case: TestCase, arm: Arm, run_index: int) -> RunResult:
    workspace = Path(tempfile.mkdtemp(prefix=f"skill-eval-{case.id}-{arm.key}-"))
    try:
        if case.fixture_dir is not None:
            shutil.copytree(case.fixture_dir, workspace, dirs_exist_ok=True)
        start = time.monotonic()
        try:
            proc = subprocess.run(
                _build_cmd(cfg, arm, case.prompt),
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=cfg.timeout_s,
            )
        except subprocess.TimeoutExpired:
            return RunResult(
                case_id=case.id,
                arm=arm.key,
                run_index=run_index,
                ok=False,
                output="",
                latency_s=time.monotonic() - start,
                error=f"timeout after {cfg.timeout_s}s",
            )
        latency = time.monotonic() - start

        if proc.returncode != 0:
            return RunResult(
                case_id=case.id,
                arm=arm.key,
                run_index=run_index,
                ok=False,
                output=proc.stdout,
                latency_s=latency,
                error=f"exit {proc.returncode}: {proc.stderr.strip()[:2000]}",
            )

        try:
            output, usage = _parse_output(proc.stdout, cfg.executor.output_format)
        except (json.JSONDecodeError, TypeError) as exc:
            return RunResult(
                case_id=case.id,
                arm=arm.key,
                run_index=run_index,
                ok=False,
                output=proc.stdout,
                latency_s=latency,
                error=f"unparseable claude output: {exc}",
            )

        check_ok: bool | None = None
        if case.check_cmd is not None:
            check = subprocess.run(
                case.check_cmd,
                shell=True,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=120,
            )
            check_ok = check.returncode == 0

        return RunResult(
            case_id=case.id,
            arm=arm.key,
            run_index=run_index,
            ok=True,
            output=output,
            latency_s=latency,
            check_cmd_ok=check_ok,
            usage=usage,
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def result_path(results_dir: Path, case_id: str, arm_key: str, run_index: int) -> Path:
    return results_dir / case_id / arm_key / f"run-{run_index}.json"


def save_result(results_dir: Path, result: RunResult) -> Path:
    path = result_path(results_dir, result.case_id, result.arm, result.run_index)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_results(results_dir: Path) -> list[RunResult]:
    results: list[RunResult] = []
    for path in sorted(results_dir.glob("*/*/run-*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        results.append(RunResult(**raw))
    return results


def run_all(cfg: EvalConfig, resume: bool = True) -> list[RunResult]:
    """Run every (case, arm, run) combination; with resume=True, combinations
    that already have a saved result on disk are skipped."""
    results: list[RunResult] = []
    total = len(cfg.cases) * len(cfg.arms) * cfg.runs_per_case
    done = 0
    for case in cfg.cases:
        for arm in cfg.arms:
            for run_index in range(cfg.runs_per_case):
                done += 1
                existing = result_path(cfg.results_dir, case.id, arm.key, run_index)
                if resume and existing.is_file():
                    results.append(RunResult(**json.loads(existing.read_text(encoding="utf-8"))))
                    print(f"[{done}/{total}] {case.id}/{arm.key}/run-{run_index} (cached)")
                    continue
                print(f"[{done}/{total}] {case.id}/{arm.key}/run-{run_index} ...", flush=True)
                result = run_once(cfg, case, arm, run_index)
                save_result(cfg.results_dir, result)
                status = "ok" if result.ok else f"FAILED: {result.error}"
                print(f"[{done}/{total}] {case.id}/{arm.key}/run-{run_index} {status} ({result.latency_s:.1f}s)")
                results.append(result)
    return results
