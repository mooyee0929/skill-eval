from pathlib import Path

import pytest

from skill_eval.config import load_eval_config, load_taxonomy

ROOT = Path(__file__).parent.parent


def test_taxonomy_loads_and_is_consistent() -> None:
    taxonomy = load_taxonomy(ROOT / "taxonomy.yaml")
    assert "summarization" in taxonomy.categories
    for cat in taxonomy.categories.values():
        for metric_name in cat.metrics:
            metric = taxonomy.metrics[metric_name]
            if metric.kind == "judge":
                assert metric.rubric, f"judge metric {metric_name} needs a rubric"
            if metric.kind == "deterministic" and metric_name != "render_success_rate":
                # render_success_rate is output-only and needs no expected block
                assert metric.expected_type, f"{metric_name} needs expected_type"


def write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "eval.yaml"
    path.write_text(body, encoding="utf-8")
    return path


VALID = """\
mode: skill-vs-baseline
purpose: test purpose
category: extraction
arms:
  - label: baseline
  - label: with skill
    skill: skill.md
cases:
  - id: c1
    prompt: extract the id
    expected:
      type: fields
      value: {{id: "42"}}
"""


def test_valid_config_roundtrip(tmp_path: Path) -> None:
    (tmp_path / "skill.md").write_text("# s", encoding="utf-8")
    cfg = load_eval_config(write_config(tmp_path, VALID.format()))
    assert cfg.mode == "skill-vs-baseline"
    assert cfg.arms[0].skill_path is None
    assert cfg.arms[1].skill_path is not None
    assert cfg.cases[0].expected is not None
    assert cfg.cases[0].expected.value == {"id": "42"}
    assert cfg.results_dir == (tmp_path / "results").resolve()


def test_missing_skill_file_rejected(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_eval_config(write_config(tmp_path, VALID.format()))


def test_duplicate_case_ids_rejected(tmp_path: Path) -> None:
    (tmp_path / "skill.md").write_text("# s", encoding="utf-8")
    body = VALID.format() + """\
  - id: c1
    prompt: duplicate
"""
    with pytest.raises(ValueError, match="duplicate case id"):
        load_eval_config(write_config(tmp_path, body))


def test_baseline_mode_requires_a_bare_arm(tmp_path: Path) -> None:
    (tmp_path / "skill.md").write_text("# s", encoding="utf-8")
    body = VALID.format().replace("- label: baseline", "- label: baseline\n    skill: skill.md")
    with pytest.raises(ValueError, match="requires one arm without"):
        load_eval_config(write_config(tmp_path, body))
