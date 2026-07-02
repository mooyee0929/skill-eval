"""skill-eval CLI.

    skill-eval categories                 list taxonomy categories and their metrics
    skill-eval validate  -c eval.yaml     check config + taxonomy consistency
    skill-eval gen-cases -c eval.yaml     parallel independent generators -> candidates.yaml
    skill-eval add-case  -c eval.yaml --from candidates.yaml --ids a,b   merge approved cases
    skill-eval run       -c eval.yaml     execute all (case, arm, run) combinations
    skill-eval score     -c eval.yaml     score saved runs -> results/scores.json
    skill-eval report    -c eval.yaml     aggregate -> results/report.md (printed too)
"""

from __future__ import annotations

import argparse
import pkgutil
import sys
from pathlib import Path

from . import aggregate as agg
from .config import EvalConfig, Taxonomy, load_eval_config, load_taxonomy, load_taxonomy_text
from .runner import load_results, run_all


def _resolve_taxonomy(arg: str | None) -> Taxonomy:
    """--taxonomy path if given, else the copy bundled with the package
    (works from a normal install and from a zipapp)."""
    if arg:
        return load_taxonomy(Path(arg))
    data = pkgutil.get_data("skill_eval", "taxonomy.yaml")
    if data is None:
        raise FileNotFoundError("bundled taxonomy.yaml not found in package")
    return load_taxonomy_text(data.decode("utf-8"))


def _load(args: argparse.Namespace) -> tuple[EvalConfig, Taxonomy]:
    taxonomy = _resolve_taxonomy(args.taxonomy)
    cfg = load_eval_config(Path(args.config))
    taxonomy.category(cfg.category)  # fail fast on unknown category
    return cfg, taxonomy


def cmd_categories(args: argparse.Namespace) -> int:
    taxonomy = _resolve_taxonomy(args.taxonomy)
    for key, cat in taxonomy.categories.items():
        print(f"{key}: {cat.title}")
        for metric, weight in cat.metrics.items():
            kind = taxonomy.metrics[metric].kind
            print(f"  - {metric} (w={weight}, {kind})")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    cfg, taxonomy = _load(args)
    category = taxonomy.category(cfg.category)
    det = [m for m in category.metrics if taxonomy.metrics[m].kind == "deterministic"]
    covered = 0
    for case in cfg.cases:
        applicable = case.expected is not None or case.check_cmd is not None
        if applicable:
            covered += 1
        else:
            print(f"note: case '{case.id}' has no expected/check_cmd -> judge metrics only")
    print(
        f"ok: mode={cfg.mode}, category={cfg.category}, "
        f"{len(cfg.cases)} cases ({covered} with deterministic ground truth), "
        f"{len(det)} deterministic metrics in category, "
        f"{cfg.runs_per_case} runs/case, model={cfg.model}"
    )
    return 0


def cmd_gen_cases(args: argparse.Namespace) -> int:
    from .gencases import LENSES, generate_candidates, write_candidates

    cfg, _ = _load(args)
    lenses = args.lenses.split(",") if args.lenses else sorted(LENSES)
    out = Path(args.out) if args.out else Path(args.config).parent / "candidates.yaml"
    print(f"generating {args.per_lens} case(s) × {len(lenses)} independent lens generators...")
    candidates, warnings = generate_candidates(cfg, lenses=lenses, per_lens=args.per_lens)
    for warning in warnings:
        print(f"  note: {warning}")
    write_candidates(out, candidates)
    print(f"done: {len(candidates)} candidates -> {out}")
    print("review them, then merge approved ones with: skill-eval add-case "
          f"-c {args.config} --from {out} --ids <id1,id2,...>")
    return 0


def cmd_add_case(args: argparse.Namespace) -> int:
    from .gencases import merge_cases

    if not args.all and not args.ids:
        print("provide --ids id1,id2,... or --all", file=sys.stderr)
        return 1
    ids = None if args.all else args.ids.split(",")
    added, skipped = merge_cases(Path(args.config), Path(args.from_path), ids)
    for message in skipped:
        print(f"  note: {message}")
    print(f"added {added} case(s) to {args.config}")
    _load(args)  # re-validate the merged config
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    cfg, _ = _load(args)
    results = run_all(cfg, resume=not args.no_resume)
    failed = [r for r in results if not r.ok]
    print(f"\ndone: {len(results)} runs, {len(failed)} failed -> {cfg.results_dir}")
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    cfg, taxonomy = _load(args)
    results = load_results(cfg.results_dir)
    if not results:
        print(f"no results under {cfg.results_dir} — run `skill-eval run` first", file=sys.stderr)
        return 1
    print(f"scoring {len(results)} runs (judge: {cfg.judge_model} × {cfg.judge_votes} votes)...")
    table = agg.score_results(cfg, taxonomy, results)
    out = cfg.results_dir / "scores.json"
    agg.save_scores(out, table)
    print(f"done: {len(table.scores)} metric scores -> {out}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    cfg, taxonomy = _load(args)
    scores_path = cfg.results_dir / "scores.json"
    if not scores_path.is_file():
        print(f"{scores_path} missing — run `skill-eval score` first", file=sys.stderr)
        return 1
    table = agg.load_scores(scores_path)
    report = agg.aggregate(cfg, taxonomy, table)
    markdown = agg.render_markdown(cfg, taxonomy, report)
    out = cfg.results_dir / "report.md"
    out.write_text(markdown, encoding="utf-8")
    print(markdown)
    print(f"saved -> {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="skill-eval", description=__doc__)
    parser.add_argument(
        "--taxonomy", default=None, help="path to taxonomy.yaml (default: bundled copy)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("categories", help="list categories and metrics")
    for name in ("validate", "gen-cases", "add-case", "run", "score", "report"):
        p = sub.add_parser(name)
        p.add_argument("-c", "--config", required=True, help="path to eval.yaml")
        if name == "run":
            p.add_argument(
                "--no-resume", action="store_true", help="re-run combinations that already have results"
            )
        elif name == "gen-cases":
            p.add_argument("--lenses", default=None, help="comma-separated lens list (default: all)")
            p.add_argument("--per-lens", type=int, default=2, help="cases per lens (default 2)")
            p.add_argument("--out", default=None, help="candidates file (default: candidates.yaml next to config)")
        elif name == "add-case":
            p.add_argument("--from", dest="from_path", required=True, help="candidates yaml file")
            p.add_argument("--ids", default=None, help="comma-separated candidate ids to merge")
            p.add_argument("--all", action="store_true", help="merge every candidate")

    args = parser.parse_args(argv)
    handlers = {
        "categories": cmd_categories,
        "validate": cmd_validate,
        "gen-cases": cmd_gen_cases,
        "add-case": cmd_add_case,
        "run": cmd_run,
        "score": cmd_score,
        "report": cmd_report,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
