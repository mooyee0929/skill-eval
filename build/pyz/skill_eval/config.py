"""Typed loading of eval.yaml (the per-evaluation config) and taxonomy.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

Mode = Literal["skill-vs-skill", "skill-vs-baseline"]
ExpectedType = Literal[
    "exact", "fields", "ranking", "text", "edges", "diagram", "links", "checks"
]


@dataclass(frozen=True)
class MetricDef:
    name: str
    kind: Literal["deterministic", "judge", "meta"]
    direction: Literal["higher", "lower"]
    expected_type: ExpectedType | None = None
    rubric: str | None = None


@dataclass(frozen=True)
class Category:
    key: str
    title: str
    metrics: dict[str, float]  # metric name -> weight


@dataclass(frozen=True)
class Taxonomy:
    metrics: dict[str, MetricDef]
    categories: dict[str, Category]

    def category(self, key: str) -> Category:
        if key not in self.categories:
            known = ", ".join(sorted(self.categories))
            raise KeyError(f"unknown category '{key}' (known: {known})")
        return self.categories[key]


@dataclass(frozen=True)
class Arm:
    """One side of the comparison: a skill file, or a bare baseline."""

    key: str  # "a" | "b"
    label: str
    skill_path: Path | None  # None => baseline arm (no skill appended)


@dataclass(frozen=True)
class Executor:
    """How to invoke the model CLI. Defaults to the claude CLI; set command
    templates to run the evaluation on any other agent CLI.

    Template placeholders: {prompt}, {model}, {max_turns}, {skill}.
    Entries containing {skill} (plus an immediately preceding flag entry)
    are dropped for arms without a skill. If no entry contains {skill},
    the skill content is prepended to the prompt instead."""

    run_cmd: list[str] | None = None
    judge_cmd: list[str] | None = None
    output_format: Literal["claude-json", "text"] = "claude-json"


@dataclass(frozen=True)
class Expected:
    type: ExpectedType
    value: Any
    k: int | None = None  # for ranking metrics


@dataclass(frozen=True)
class TestCase:
    id: str
    prompt: str
    lens: str = "happy-path"
    expected: Expected | None = None
    fixture_dir: Path | None = None  # copied into an isolated workspace per run
    check_cmd: str | None = None  # for update_success_rate: exit 0 = success


@dataclass(frozen=True)
class EvalConfig:
    mode: Mode
    purpose: str
    requirements: list[str]
    category: str
    arms: list[Arm]
    cases: list[TestCase]
    runs_per_case: int = 3
    model: str = "claude-sonnet-4-6"
    judge_model: str = "claude-haiku-4-5-20251001"
    judge_votes: int = 3
    max_turns: int = 8
    permission_mode: str | None = None
    timeout_s: int = 600
    results_dir: Path = field(default_factory=lambda: Path("results"))
    executor: Executor = field(default_factory=Executor)


def load_taxonomy(path: Path) -> Taxonomy:
    return load_taxonomy_text(path.read_text(encoding="utf-8"))


def load_taxonomy_text(text: str) -> Taxonomy:
    raw = yaml.safe_load(text)
    metrics: dict[str, MetricDef] = {}
    for name, m in raw["metrics"].items():
        metrics[name] = MetricDef(
            name=name,
            kind=m["kind"],
            direction=m["direction"],
            expected_type=m.get("expected_type"),
            rubric=m.get("rubric"),
        )
    categories: dict[str, Category] = {}
    for key, c in raw["categories"].items():
        for metric_name in c["metrics"]:
            if metric_name not in metrics:
                raise ValueError(
                    f"category '{key}' references undefined metric '{metric_name}'"
                )
        categories[key] = Category(key=key, title=c["title"], metrics=dict(c["metrics"]))
    return Taxonomy(metrics=metrics, categories=categories)


def _load_expected(raw: dict[str, Any] | None) -> Expected | None:
    if raw is None:
        return None
    return Expected(type=raw["type"], value=raw["value"], k=raw.get("k"))


def load_eval_config(path: Path) -> EvalConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    base = path.parent

    mode: Mode = raw["mode"]
    arms_raw = raw["arms"]
    if len(arms_raw) != 2:
        raise ValueError(f"exactly 2 arms required, got {len(arms_raw)}")
    arms: list[Arm] = []
    for key, a in zip(("a", "b"), arms_raw):
        skill = a.get("skill")
        skill_path = (base / skill).resolve() if skill else None
        if skill_path is not None and not skill_path.is_file():
            raise FileNotFoundError(f"arm '{a['label']}': skill file not found: {skill_path}")
        arms.append(Arm(key=key, label=a["label"], skill_path=skill_path))
    if mode == "skill-vs-baseline" and all(a.skill_path for a in arms):
        raise ValueError("skill-vs-baseline mode requires one arm without a skill file")

    cases: list[TestCase] = []
    seen_ids: set[str] = set()
    for c in raw["cases"]:
        if c["id"] in seen_ids:
            raise ValueError(f"duplicate case id '{c['id']}'")
        seen_ids.add(c["id"])
        fixture = c.get("fixture_dir")
        cases.append(
            TestCase(
                id=c["id"],
                prompt=c["prompt"],
                lens=c.get("lens", "happy-path"),
                expected=_load_expected(c.get("expected")),
                fixture_dir=(base / fixture).resolve() if fixture else None,
                check_cmd=c.get("check_cmd"),
            )
        )
    if not cases:
        raise ValueError("at least one test case is required")

    executor_raw = raw.get("executor", {})
    executor = Executor(
        run_cmd=executor_raw.get("run_cmd"),
        judge_cmd=executor_raw.get("judge_cmd"),
        output_format=executor_raw.get("output_format", "claude-json"),
    )
    if executor.run_cmd is not None and not any("{prompt}" in p for p in executor.run_cmd):
        raise ValueError("executor.run_cmd must contain a {prompt} placeholder")
    if executor.judge_cmd is not None and not any("{prompt}" in p for p in executor.judge_cmd):
        raise ValueError("executor.judge_cmd must contain a {prompt} placeholder")

    return EvalConfig(
        mode=mode,
        purpose=raw["purpose"],
        requirements=list(raw.get("requirements", [])),
        category=raw["category"],
        arms=arms,
        cases=cases,
        runs_per_case=int(raw.get("runs_per_case", 3)),
        model=raw.get("model", "claude-sonnet-4-6"),
        judge_model=raw.get("judge_model", "claude-haiku-4-5-20251001"),
        judge_votes=int(raw.get("judge_votes", 3)),
        max_turns=int(raw.get("max_turns", 8)),
        permission_mode=raw.get("permission_mode"),
        timeout_s=int(raw.get("timeout_s", 600)),
        results_dir=(base / raw.get("results_dir", "results")).resolve(),
        executor=executor,
    )
