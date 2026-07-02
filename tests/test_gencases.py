from pathlib import Path

import pytest
import yaml

from skill_eval.gencases import (
    generate_candidates,
    merge_cases,
    parse_candidates,
    write_candidates,
)
from tests.test_aggregate import make_cfg

CANDIDATE_YAML = """\
- id: happy-one
  lens: happy-path
  capability_point: extracts a plain id
  prompt: |
    Extract the id from: order 42
  expected:
    type: exact
    value: "42"
- id: happy-two
  lens: happy-path
  capability_point: extracts a plain id
  prompt: |
    Extract the id from: order 43
"""


def test_parse_candidates_plain_and_fenced() -> None:
    assert len(parse_candidates(CANDIDATE_YAML)) == 2
    fenced = "Here you go:\n```yaml\n" + CANDIDATE_YAML + "```"
    assert len(parse_candidates(fenced)) == 2
    with pytest.raises(ValueError):
        parse_candidates("just: a mapping")


def test_generate_candidates_dedupes(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path)

    def fake_complete(executor, prompt, *, model, timeout_s):  # type: ignore[no-untyped-def]
        return CANDIDATE_YAML

    candidates, warnings = generate_candidates(
        cfg, lenses=["happy-path", "edge"], per_lens=2, _complete=fake_complete
    )
    # both lenses return the same two candidates; duplicate ids and duplicate
    # capability points collapse to a single survivor
    assert [c["id"] for c in candidates] == ["happy-one"]
    assert any("duplicate id" in w for w in warnings)
    assert any("already covered" in w for w in warnings)


def test_generate_candidates_rejects_unknown_lens(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path)
    with pytest.raises(ValueError, match="unknown lenses"):
        generate_candidates(cfg, lenses=["nope"], per_lens=1)


def test_merge_cases_selected_ids(tmp_path: Path) -> None:
    config = tmp_path / "eval.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "mode": "skill-vs-baseline",
                "purpose": "p",
                "category": "extraction",
                "arms": [{"label": "a"}, {"label": "b", "skill": "skill.md"}],
                "cases": [{"id": "existing", "prompt": "x"}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    candidates = tmp_path / "candidates.yaml"
    write_candidates(candidates, parse_candidates(CANDIDATE_YAML))

    added, skipped = merge_cases(config, candidates, ids=["happy-one"])
    assert added == 1 and skipped == []
    merged = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert [c["id"] for c in merged["cases"]] == ["existing", "happy-one"]
    # multiline prompt survives the round-trip
    assert "order 42" in merged["cases"][1]["prompt"]

    added, skipped = merge_cases(config, candidates, ids=["happy-one"])
    assert added == 0 and len(skipped) == 1

    with pytest.raises(ValueError, match="not found"):
        merge_cases(config, candidates, ids=["ghost"])
