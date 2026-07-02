from pathlib import Path

import pytest

from skill_eval.aggregate import MetricScore, ScoreTable, aggregate, render_markdown
from skill_eval.config import (
    Arm,
    EvalConfig,
    Expected,
    TestCase,
    load_taxonomy,
)

TAXONOMY = load_taxonomy(Path(__file__).parent.parent / "taxonomy.yaml")


def make_cfg(tmp_path: Path, category: str = "extraction") -> EvalConfig:
    skill = tmp_path / "skill.md"
    skill.write_text("# skill", encoding="utf-8")
    return EvalConfig(
        mode="skill-vs-baseline",
        purpose="test",
        requirements=[],
        category=category,
        arms=[
            Arm(key="a", label="baseline", skill_path=None),
            Arm(key="b", label="skill", skill_path=skill),
        ],
        cases=[
            TestCase(id="c1", prompt="p1", expected=Expected(type="fields", value={"x": "1"})),
            TestCase(id="c2", prompt="p2", expected=Expected(type="exact", value="y")),
        ],
        runs_per_case=1,
        results_dir=tmp_path / "results",
    )


def make_table() -> ScoreTable:
    return ScoreTable(
        scores=[
            MetricScore("c1", "a", 0, "field_f1", 0.5),
            MetricScore("c1", "b", 0, "field_f1", 1.0),
            MetricScore("c2", "a", 0, "exact_match", 0.0),
            MetricScore("c2", "b", 0, "exact_match", 1.0),
        ]
    )


def test_aggregate_composite_and_delta(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path)
    report = aggregate(cfg, TAXONOMY, make_table())
    # extraction weights: field_f1 0.5, exact_match 0.5
    assert report.composite["a"] == pytest.approx(0.25)
    assert report.composite["b"] == pytest.approx(1.0)
    assert report.delta == pytest.approx(0.75)
    assert report.regressions == []


def test_aggregate_flags_regressions(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path)
    table = make_table()
    table.scores[2] = MetricScore("c2", "a", 0, "exact_match", 1.0)
    table.scores[3] = MetricScore("c2", "b", 0, "exact_match", 0.0)
    report = aggregate(cfg, TAXONOMY, table)
    assert "c2" in report.regressions


def test_lower_direction_metric_normalized(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path, category="doc_update_workflow")
    table = ScoreTable(
        scores=[
            MetricScore("c1", "a", 0, "broken_link_rate", 0.4),
            MetricScore("c1", "b", 0, "broken_link_rate", 0.1),
            MetricScore("c1", "a", 0, "latency", 10.0),
            MetricScore("c1", "b", 0, "latency", 20.0),
        ]
    )
    report = aggregate(cfg, TAXONOMY, table)
    by_name = {s.metric: s for s in report.metrics}
    # lower is better: fewer broken links -> higher normalized score
    assert by_name["broken_link_rate"].normalized["b"] == pytest.approx(0.9)
    assert by_name["broken_link_rate"].normalized["a"] == pytest.approx(0.6)
    # latency normalized as fastest/mean
    assert by_name["latency"].normalized["a"] == pytest.approx(1.0)
    assert by_name["latency"].normalized["b"] == pytest.approx(0.5)


def test_render_markdown_contains_key_sections(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path)
    report = aggregate(cfg, TAXONOMY, make_table())
    md = render_markdown(cfg, TAXONOMY, report)
    assert "## Composite" in md
    assert "## Per-metric" in md
    assert "baseline" in md and "skill" in md
    assert "treat the uplift as directional" in md  # <10 cases warning
