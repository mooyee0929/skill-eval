from pathlib import Path

import pytest

from skill_eval.config import Expected
from skill_eval.scorers import deterministic as d


def test_exact_match() -> None:
    exp = Expected(type="exact", value="ORD-12345")
    assert d.exact_match("ord-12345 ", exp) == 1.0
    assert d.exact_match("ORD-99999", exp) == 0.0


def test_field_f1_perfect_and_partial() -> None:
    exp = Expected(type="fields", value={"id": "42", "name": "pump"})
    assert d.field_f1('{"id": "42", "name": "pump"}', exp) == 1.0
    # one of two fields correct, one extra wrong field
    score = d.field_f1('{"id": "42", "name": "valve"}', exp)
    assert score == pytest.approx(0.5)
    assert d.field_f1("no json here", exp) == 0.0


def test_field_f1_fenced_json() -> None:
    exp = Expected(type="fields", value={"id": "42"})
    out = 'Here you go:\n```json\n{"id": "42"}\n```'
    assert d.field_f1(out, exp) == 1.0


def test_ranking_metrics() -> None:
    exp = Expected(type="ranking", value=["src/auth.py", "src/token.py"], k=3)
    out = "1. `src/auth.py`\n2. src/db.py\n3. src/token.py\n4. src/misc.py"
    assert d.precision_at_k(out, exp) == pytest.approx(2 / 3)
    assert d.hit_at_k(out, exp) == 1.0
    assert d.mrr(out, exp) == 1.0


def test_mrr_second_position() -> None:
    exp = Expected(type="ranking", value=["target.py"])
    out = '["other.py", "target.py"]'
    assert d.mrr(out, exp) == pytest.approx(0.5)


def test_rouge_l_identical_and_disjoint() -> None:
    exp = Expected(type="text", value="the module adds two integers")
    assert d.rouge_l("the module adds two integers", exp) == 1.0
    assert d.rouge_l("completely unrelated words here", exp) == 0.0


def test_edge_f1_mermaid() -> None:
    exp = Expected(type="edges", value=["A -> B", "B -> C"])
    out = "```mermaid\ngraph TD\n  A --> B\n  B --> C\n  C --> D\n```"
    score = d.edge_f1(out, exp)
    # got 3 edges, 2 correct: p=2/3, r=1 -> f1=0.8
    assert score == pytest.approx(0.8)
    assert d.edge_f1("no diagram", exp) == 0.0


def test_render_success_rate() -> None:
    exp = Expected(type="diagram", value=None)
    assert d.render_success_rate("```mermaid\ngraph TD\n A --> B\n```", exp) == 1.0
    assert d.render_success_rate("```mermaid\njust text\n```", exp) == 0.0
    assert d.render_success_rate("no block", exp) == 0.0


def test_broken_link_rate(tmp_path: Path) -> None:
    (tmp_path / "exists.md").write_text("x", encoding="utf-8")
    exp = Expected(type="links", value=str(tmp_path))
    out = "See [a](exists.md) and [b](missing.md) and [ext](https://example.com)"
    assert d.broken_link_rate(out, exp) == pytest.approx(0.5)
    assert d.broken_link_rate("no links at all", exp) is None


def test_update_success_rate_prefers_check_cmd() -> None:
    exp = Expected(type="checks", value=["version 2.0"])
    assert d.update_success_rate("nothing relevant", exp, check_cmd_ok=True) == 1.0
    assert d.update_success_rate("mentions Version 2.0 here", exp) == 1.0
    assert d.update_success_rate("nope", exp) == 0.0


def test_score_dispatch() -> None:
    exp = Expected(type="exact", value="hello")
    assert d.score("exact_match", "hello", exp) == 1.0
    assert d.score("nonexistent_metric", "x", exp) is None
    assert d.score("field_f1", "x", None) is None
