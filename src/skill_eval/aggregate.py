"""Scoring orchestration and aggregation: run outputs -> per-metric scores ->
normalized weighted composite per arm -> uplift report."""

from __future__ import annotations

import json
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import EvalConfig, MetricDef, Taxonomy, TestCase
from .runner import RunResult
from .scorers import deterministic
from .scorers.judge import JudgeError, judge_score


@dataclass(frozen=True)
class MetricScore:
    case_id: str
    arm: str
    run_index: int
    metric: str
    value: float


@dataclass
class ScoreTable:
    scores: list[MetricScore] = field(default_factory=list)

    def values(self, arm: str, metric: str, case_id: str | None = None) -> list[float]:
        return [
            s.value
            for s in self.scores
            if s.arm == arm
            and s.metric == metric
            and (case_id is None or s.case_id == case_id)
        ]


def _case_by_id(cfg: EvalConfig) -> dict[str, TestCase]:
    return {c.id: c for c in cfg.cases}


def score_results(
    cfg: EvalConfig,
    taxonomy: Taxonomy,
    results: list[RunResult],
    *,
    verbose: bool = True,
    judge_workers: int = 12,
) -> ScoreTable:
    category = taxonomy.category(cfg.category)
    cases = _case_by_id(cfg)
    table = ScoreTable()
    judge_jobs: list[tuple[TestCase, RunResult, MetricDef]] = []

    for result in results:
        case = cases.get(result.case_id)
        if case is None:
            continue
        for metric_name in category.metrics:
            metric = taxonomy.metrics[metric_name]
            if metric.kind == "judge" and result.ok:
                judge_jobs.append((case, result, metric))
                continue
            value = _score_one(cfg, metric, case, result, verbose=verbose)
            if value is not None:
                table.scores.append(
                    MetricScore(
                        case_id=result.case_id,
                        arm=result.arm,
                        run_index=result.run_index,
                        metric=metric_name,
                        value=value,
                    )
                )

    if judge_jobs:
        if verbose:
            print(f"  {len(judge_jobs)} judge jobs × {cfg.judge_votes} votes ({judge_workers} parallel workers)")
        done = 0
        with ThreadPoolExecutor(max_workers=judge_workers) as pool:
            futures = {
                pool.submit(
                    judge_score,
                    metric,
                    case.prompt,
                    result.output,
                    model=cfg.judge_model,
                    votes=cfg.judge_votes,
                ): (case, result, metric)
                for case, result, metric in judge_jobs
            }
            for future in as_completed(futures):
                case, result, metric = futures[future]
                done += 1
                try:
                    value = future.result()
                except JudgeError as exc:
                    if verbose:
                        print(f"  judge failed for {case.id}/{result.arm}/{metric.name}: {exc}")
                    continue
                table.scores.append(
                    MetricScore(
                        case_id=result.case_id,
                        arm=result.arm,
                        run_index=result.run_index,
                        metric=metric.name,
                        value=value,
                    )
                )
                if verbose and done % 20 == 0:
                    print(f"  judged {done}/{len(judge_jobs)}")
    return table


def _score_one(
    cfg: EvalConfig,
    metric: MetricDef,
    case: TestCase,
    result: RunResult,
    *,
    verbose: bool,
) -> float | None:
    if metric.kind == "meta":
        if metric.name == "latency":
            return result.latency_s
        if metric.name == "failure_rate":
            return 0.0 if result.ok else 1.0
        return None

    if not result.ok:
        # A failed run scores 0 on every quality metric for that case.
        return 0.0

    if metric.kind == "deterministic":
        # Metrics without an expected_type score every case from the output alone.
        if (
            metric.expected_type is not None
            and case.expected is not None
            and case.expected.type != metric.expected_type
            and metric.name != "update_success_rate"
        ):
            return None
        return deterministic.score(
            metric.name, result.output, case.expected, check_cmd_ok=result.check_cmd_ok
        )

    # judge metrics on successful runs are scored in parallel by score_results
    return None


def save_scores(path: Path, table: ScoreTable) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(s) for s in table.scores], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_scores(path: Path) -> ScoreTable:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ScoreTable(scores=[MetricScore(**s) for s in raw])


# --- aggregation -------------------------------------------------------------


@dataclass(frozen=True)
class MetricSummary:
    metric: str
    weight: float
    direction: str
    mean: dict[str, float | None]  # arm -> raw mean
    normalized: dict[str, float | None]  # arm -> [0,1], higher = better
    n: dict[str, int]


@dataclass(frozen=True)
class Report:
    composite: dict[str, float]  # arm -> weighted composite [0,1]
    delta: float  # arm b - arm a
    metrics: list[MetricSummary]
    regressions: list[str]  # case ids where arm b underperforms arm a


def _normalize(
    metric: MetricDef, means: dict[str, float | None]
) -> dict[str, float | None]:
    if metric.name == "latency":
        valid = [v for v in means.values() if v is not None and v > 0]
        floor = min(valid) if valid else None
        return {
            arm: (floor / v if v is not None and v > 0 and floor is not None else None)
            for arm, v in means.items()
        }
    if metric.direction == "lower":
        return {arm: (1.0 - v if v is not None else None) for arm, v in means.items()}
    return dict(means)


def aggregate(cfg: EvalConfig, taxonomy: Taxonomy, table: ScoreTable) -> Report:
    category = taxonomy.category(cfg.category)
    arm_keys = [a.key for a in cfg.arms]

    summaries: list[MetricSummary] = []
    for metric_name, weight in category.metrics.items():
        metric = taxonomy.metrics[metric_name]
        means: dict[str, float | None] = {}
        counts: dict[str, int] = {}
        for arm in arm_keys:
            vals = table.values(arm, metric_name)
            means[arm] = statistics.fmean(vals) if vals else None
            counts[arm] = len(vals)
        summaries.append(
            MetricSummary(
                metric=metric_name,
                weight=weight,
                direction=metric.direction,
                mean=means,
                normalized=_normalize(metric, means),
                n=counts,
            )
        )

    composite: dict[str, float] = {}
    for arm in arm_keys:
        weighted = [
            (s.weight, s.normalized[arm])
            for s in summaries
            if s.normalized[arm] is not None
        ]
        total_w = sum(w for w, _ in weighted)
        composite[arm] = (
            sum(w * v for w, v in weighted if v is not None) / total_w if total_w else 0.0
        )

    regressions: list[str] = []
    if len(arm_keys) == 2:
        a, b = arm_keys
        for case in cfg.cases:
            deltas: list[float] = []
            for s in summaries:
                va = table.values(a, s.metric, case.id)
                vb = table.values(b, s.metric, case.id)
                if not va or not vb:
                    continue
                d = statistics.fmean(vb) - statistics.fmean(va)
                deltas.append(-d if s.direction == "lower" else d)
            if deltas and statistics.fmean(deltas) < 0:
                regressions.append(case.id)

    return Report(
        composite=composite,
        delta=composite[arm_keys[1]] - composite[arm_keys[0]],
        metrics=summaries,
        regressions=regressions,
    )


def render_markdown(cfg: EvalConfig, taxonomy: Taxonomy, report: Report) -> str:
    category = taxonomy.category(cfg.category)
    labels = {a.key: a.label for a in cfg.arms}
    a, b = (arm.key for arm in cfg.arms)

    def fmt(v: float | None) -> str:
        return f"{v:.3f}" if v is not None else "—"

    lines = [
        "# Skill evaluation report",
        "",
        f"- **Mode:** {cfg.mode}",
        f"- **Category:** {category.title}",
        f"- **Purpose:** {cfg.purpose}",
        f"- **Cases:** {len(cfg.cases)} × {cfg.runs_per_case} runs per arm (model: {cfg.model})",
        "",
        "## Composite",
        "",
        f"| Arm | Composite score |",
        f"|---|---|",
    ]
    for key, label in labels.items():
        lines.append(f"| {label} | **{report.composite[key]:.3f}** |")
    verdict = "improves" if report.delta > 0 else ("regresses" if report.delta < 0 else "matches")
    lines += [
        "",
        f"**Uplift ({labels[b]} vs {labels[a]}): {report.delta:+.3f}** — {labels[b]} {verdict} the comparison arm.",
        "",
        "## Per-metric",
        "",
        f"| Metric | Weight | {labels[a]} | {labels[b]} | Δ (normalized) | n |",
        "|---|---|---|---|---|---|",
    ]
    for s in report.metrics:
        na, nb = s.normalized[a], s.normalized[b]
        delta = f"{nb - na:+.3f}" if na is not None and nb is not None else "—"
        lines.append(
            f"| {s.metric} | {s.weight:.2f} | {fmt(s.mean[a])} | {fmt(s.mean[b])} "
            f"| {delta} | {s.n[a]}/{s.n[b]} |"
        )
    lines += ["", "## Regressions", ""]
    if report.regressions:
        lines.append(
            f"Cases where {labels[b]} scored below {labels[a]} (worth reading first):"
        )
        lines += [f"- `{cid}`" for cid in report.regressions]
    else:
        lines.append("None.")
    if len(cfg.cases) < 10:
        lines += [
            "",
            f"> ⚠ Only {len(cfg.cases)} test cases — treat the uplift as directional, not conclusive.",
        ]
    return "\n".join(lines) + "\n"
