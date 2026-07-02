"""Deterministic metrics computed from a run's output text vs the case's
`expected` block. Every scorer returns a float in [0, 1] or None when the
metric is not applicable to the case."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..config import Expected

_JSON_BLOCK = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)
_MERMAID_BLOCK = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL)
_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)#\s]+)[^)]*\)")
# A node reference is an ID optionally followed by a shape/label like
# NodeId["label"], NodeId(text), NodeId([stadium]) or NodeId{diamond}.
_NODE_SHAPE = r"(?:[\[({][^\n]*?[\])}]+)?"
_MERMAID_EDGE = re.compile(
    rf"^\s*([A-Za-z0-9_\-\.]+){_NODE_SHAPE}\s*[-.=]{{2,}}[>ox]?[-.=]*\s*(?:\|[^|]*\|\s*)?([A-Za-z0-9_\-\.]+)",
    re.MULTILINE,
)
_MERMAID_HEADER = re.compile(r"^\s*(graph|flowchart)\s+(TD|TB|LR|RL|BT)\b")


def extract_json(output: str) -> Any | None:
    """Pull the first JSON value out of a response: fenced block first,
    then the raw text itself."""
    for match in _JSON_BLOCK.finditer(output):
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
    stripped = output.strip()
    if stripped.startswith(("{", "[")):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return None
    return None


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def exact_match(output: str, expected: Expected) -> float:
    return 1.0 if _norm(output) == _norm(expected.value) else 0.0


def field_f1(output: str, expected: Expected) -> float | None:
    """expected.value is a dict of field -> value; the response must contain a
    JSON object. F1 over correctly extracted (field, value) pairs."""
    got = extract_json(output)
    want: dict[str, Any] = expected.value
    if not isinstance(want, dict) or not want:
        return None
    if not isinstance(got, dict):
        return 0.0
    correct = sum(1 for k, v in want.items() if k in got and _norm(got[k]) == _norm(v))
    precision = correct / len(got) if got else 0.0
    recall = correct / len(want)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _extract_ranking(output: str) -> list[str]:
    """A ranking answer is a JSON list, or a markdown numbered/bulleted list."""
    got = extract_json(output)
    if isinstance(got, list):
        return [_norm(item) for item in got]
    items: list[str] = []
    for line in output.splitlines():
        m = re.match(r"\s*(?:\d+[.)]|[-*])\s+(.+)", line)
        if m:
            items.append(_norm(re.sub(r"[`*]", "", m.group(1))))
    return items


def _ranking_hits(output: str, expected: Expected) -> tuple[list[bool], int]:
    relevant = [_norm(v) for v in expected.value]
    ranked = _extract_ranking(output)
    k = expected.k or len(relevant)
    hits = [any(rel in item or item in rel for rel in relevant) for item in ranked[:k]]
    return hits, k


def precision_at_k(output: str, expected: Expected) -> float:
    hits, k = _ranking_hits(output, expected)
    return sum(hits) / k if k else 0.0


def hit_at_k(output: str, expected: Expected) -> float:
    hits, _ = _ranking_hits(output, expected)
    return 1.0 if any(hits) else 0.0


def mrr(output: str, expected: Expected) -> float:
    relevant = [_norm(v) for v in expected.value]
    ranked = _extract_ranking(output)
    for i, item in enumerate(ranked, start=1):
        if any(rel in item or item in rel for rel in relevant):
            return 1.0 / i
    return 0.0


def rouge_l(output: str, expected: Expected) -> float:
    """ROUGE-L F1 on whitespace tokens via LCS."""
    ref = _norm(expected.value).split()
    hyp = _norm(output).split()
    if not ref or not hyp:
        return 0.0
    prev = [0] * (len(hyp) + 1)
    for r_tok in ref:
        curr = [0]
        for j, h_tok in enumerate(hyp, start=1):
            curr.append(prev[j - 1] + 1 if r_tok == h_tok else max(prev[j], curr[j - 1]))
        prev = curr
    lcs = prev[-1]
    precision = lcs / len(hyp)
    recall = lcs / len(ref)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _extract_edges(output: str) -> set[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    for block in _MERMAID_BLOCK.finditer(output):
        for m in _MERMAID_EDGE.finditer(block.group(1)):
            edges.add((_norm(m.group(1)), _norm(m.group(2))))
    return edges


def edge_f1(output: str, expected: Expected) -> float:
    """expected.value is a list of 'A -> B' edge strings."""
    want: set[tuple[str, str]] = set()
    for edge in expected.value:
        src, _, dst = str(edge).partition("->")
        want.add((_norm(src), _norm(dst)))
    got = _extract_edges(output)
    if not want:
        return 0.0
    if not got:
        return 0.0
    correct = len(want & got)
    precision = correct / len(got)
    recall = correct / len(want)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def render_success_rate(output: str, expected: Expected | None = None) -> float:
    """Cheap syntactic render proxy: 1.0 if the response contains a mermaid
    block with either at least one parseable edge, or a valid graph header
    plus at least one node line (edge-less diagrams are renderable too)."""
    blocks = _MERMAID_BLOCK.findall(output)
    if not blocks:
        return 0.0
    if _extract_edges(output):
        return 1.0
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if lines and _MERMAID_HEADER.match(lines[0]) and len(lines) > 1:
            return 1.0
    return 0.0


def broken_link_rate(output: str, expected: Expected) -> float | None:
    """Fraction of relative links in the response that don't resolve under the
    case's link root (expected.value = path to the doc/repo root)."""
    root = Path(str(expected.value))
    links = [l for l in _MD_LINK.findall(output) if not re.match(r"[a-z]+://", l)]
    if not links:
        return None
    broken = sum(1 for l in links if not (root / l).exists())
    return broken / len(links)


def update_success_rate(output: str, expected: Expected, check_cmd_ok: bool | None = None) -> float | None:
    """Prefers the case's check_cmd result (exit 0 in the run workspace);
    falls back to expected.value substrings that must all appear in the output."""
    if check_cmd_ok is not None:
        return 1.0 if check_cmd_ok else 0.0
    if isinstance(expected.value, list) and expected.value:
        found = sum(1 for needle in expected.value if _norm(needle) in _norm(output))
        return found / len(expected.value)
    return None


SCORERS = {
    "exact_match": exact_match,
    "field_f1": field_f1,
    "precision_at_k": precision_at_k,
    "hit_at_k": hit_at_k,
    "mrr": mrr,
    "rouge_l": rouge_l,
    "edge_f1": edge_f1,
    "render_success_rate": render_success_rate,
    "broken_link_rate": broken_link_rate,
}


def score(metric_name: str, output: str, expected: Expected | None, *, check_cmd_ok: bool | None = None) -> float | None:
    if metric_name == "render_success_rate":
        return render_success_rate(output)
    if metric_name == "update_success_rate":
        if expected is None and check_cmd_ok is None:
            return None
        dummy = expected if expected is not None else Expected(type="checks", value=[])
        return update_success_rate(output, dummy, check_cmd_ok=check_cmd_ok)
    scorer = SCORERS.get(metric_name)
    if scorer is None or expected is None:
        return None
    return scorer(output, expected)
