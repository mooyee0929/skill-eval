"""Multi-agent test-case generation inside the engine.

Each lens runs as an INDEPENDENT completion (parallel, no shared context);
generators only see the purpose, requirements, category, their lens, and a
list of one-line capability points already covered — never full existing
cases. Candidates land in a candidates file for the front-end (Claude Code,
Copilot Chat, or a human) to approve; `add-case` merges approved ones into
eval.yaml. This keeps generation independence identical across front-ends."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import yaml

from .completion import CompletionError, complete_text
from .config import EvalConfig

LENSES: dict[str, str] = {
    "happy-path": "typical, representative inputs a real user would give",
    "edge": "empty, boundary, degenerate, or structurally tricky inputs",
    "adversarial": "inputs that tempt the skill to violate its stated requirements",
    "format-variance": "the same intent expressed through different input formats and phrasings",
}

_GEN_PROMPT = """You are a test-case generator for evaluating an AI skill. Return ONLY raw YAML (no prose, no code fences).

Skill under test — purpose: {purpose}

Requirements:
{requirements}

Category: {category}

Capability points ALREADY COVERED — your cases must each test something different:
{covered}

Your assigned lens: **{lens}** — {lens_desc}.

Generate exactly {per_lens} candidate test cases as a YAML list. Schema per case:
- id: <kebab-case, prefixed "{prefix}-">
  lens: {lens}
  capability_point: <one line: what distinct ability this case tests>
  prompt: |
    <the full self-contained prompt for the system under test>
  expected:            # include ONLY if a deterministic ground truth exists
    type: <one of: exact | fields | ranking | text | edges | links | checks>
    value: <the ground truth matching that type>

Rules:
- Each capability_point must be distinct from the covered list and from your other case.
- Prompts must be self-contained; do not reference files the system cannot see.
- Prefer cases WITH a deterministic expected block; omit it only when no objective ground truth exists.
- Output raw YAML only."""

_FENCE = re.compile(r"```(?:yaml)?\s*\n(.*?)```", re.DOTALL)
_PREFIXES = {"happy-path": "happy", "edge": "edge", "adversarial": "adv", "format-variance": "fmt"}


def build_generator_prompt(cfg: EvalConfig, lens: str, per_lens: int, covered: list[str]) -> str:
    return _GEN_PROMPT.format(
        purpose=cfg.purpose.strip(),
        requirements="\n".join(f"- {r}" for r in cfg.requirements) or "- (none stated)",
        category=cfg.category,
        covered="\n".join(f"{i + 1}. {p}" for i, p in enumerate(covered)) or "(none yet)",
        lens=lens,
        lens_desc=LENSES[lens],
        per_lens=per_lens,
        prefix=_PREFIXES.get(lens, lens),
    )


def parse_candidates(text: str) -> list[dict[str, Any]]:
    stripped = text.strip()
    fence = _FENCE.search(stripped)
    if fence:
        stripped = fence.group(1)
    data = yaml.safe_load(stripped)
    if not isinstance(data, list):
        raise ValueError(f"expected a YAML list of cases, got {type(data).__name__}")
    out: list[dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict) and item.get("id") and item.get("prompt"):
            out.append(item)
    return out


def _norm_point(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def covered_points(cfg: EvalConfig) -> list[str]:
    return [c.capability_point or f"({c.lens}) {c.id}" for c in cfg.cases]


def generate_candidates(
    cfg: EvalConfig,
    *,
    lenses: list[str],
    per_lens: int,
    workers: int = 4,
    _complete=complete_text,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Run one independent generator per lens in parallel. Returns
    (deduped candidates, warnings)."""
    unknown = [l for l in lenses if l not in LENSES]
    if unknown:
        raise ValueError(f"unknown lenses: {unknown} (known: {sorted(LENSES)})")
    covered = covered_points(cfg)
    warnings: list[str] = []
    raw: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _complete,
                cfg.executor,
                build_generator_prompt(cfg, lens, per_lens, covered),
                model=cfg.model,
                timeout_s=cfg.timeout_s,
            ): lens
            for lens in lenses
        }
        for future in as_completed(futures):
            lens = futures[future]
            try:
                raw.extend(parse_candidates(future.result()))
            except (CompletionError, ValueError, yaml.YAMLError) as exc:
                warnings.append(f"lens '{lens}' failed: {exc}")

    existing_ids = {c.id for c in cfg.cases}
    seen_points = {_norm_point(p) for p in covered}
    seen_ids: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for cand in raw:
        cid = str(cand["id"])
        point = _norm_point(cand.get("capability_point", cid))
        if cid in existing_ids or cid in seen_ids:
            warnings.append(f"dropped '{cid}': duplicate id")
            continue
        if point and point in seen_points:
            warnings.append(f"dropped '{cid}': capability point already covered")
            continue
        seen_ids.add(cid)
        seen_points.add(point)
        deduped.append(cand)
    return deduped, warnings


class _BlockDumper(yaml.SafeDumper):
    pass


def _str_representer(dumper: yaml.SafeDumper, data: str):  # type: ignore[no-untyped-def]
    if "\n" in data:
        clean = "\n".join(line.rstrip() for line in data.splitlines())
        return dumper.represent_scalar("tag:yaml.org,2002:str", clean, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


_BlockDumper.add_representer(str, _str_representer)


def dump_yaml(data: Any) -> str:
    return yaml.dump(
        data, Dumper=_BlockDumper, sort_keys=False, allow_unicode=True, width=100
    )


def write_candidates(path: Path, candidates: list[dict[str, Any]]) -> None:
    path.write_text(dump_yaml(candidates), encoding="utf-8")


def merge_cases(
    config_path: Path, candidates_path: Path, ids: list[str] | None
) -> tuple[int, list[str]]:
    """Append selected candidates (all if ids is None) to eval.yaml's cases.
    Returns (added_count, skipped_messages). Rewrites the config file; YAML
    comments are not preserved."""
    raw_cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    candidates = yaml.safe_load(candidates_path.read_text(encoding="utf-8")) or []
    if not isinstance(candidates, list):
        raise ValueError(f"{candidates_path} must contain a YAML list")

    selected = [c for c in candidates if ids is None or str(c.get("id")) in ids]
    if ids is not None:
        found = {str(c.get("id")) for c in selected}
        missing = [i for i in ids if i not in found]
        if missing:
            raise ValueError(f"ids not found in {candidates_path}: {missing}")

    existing = {c["id"] for c in raw_cfg.get("cases", [])}
    skipped: list[str] = []
    added = 0
    for cand in selected:
        if cand["id"] in existing:
            skipped.append(f"skipped '{cand['id']}': already in config")
            continue
        raw_cfg.setdefault("cases", []).append(cand)
        existing.add(cand["id"])
        added += 1
    config_path.write_text(dump_yaml(raw_cfg), encoding="utf-8")
    return added, skipped
