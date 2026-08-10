"""Command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .agreement import crosscheck
from .correctness import check
from .datasets import (
    load_deep_regex,
    load_linguafranca,
    load_regexeval,
    load_tasks,
    task_from_dict,
)
from .equivalence import equivalent
from .harness import run as run_suite
from .safety import screen
from .types import Agreement, Dialect, Semantics, Task, Verdict

_LOADERS = ("regexeval", "deep-regex", "tasks")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="regexbench",
        description="Evaluate generated regular expressions.",
    )
    parser.add_argument("--version", action="version", version=f"regexbench {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    eq = sub.add_parser("eq", help="are two patterns the same language?")
    eq.add_argument("left")
    eq.add_argument("right")
    eq.add_argument("--search", action="store_true", help="compare as re.search, not fullmatch")
    eq.add_argument("--brics", action="store_true", help="read dk.brics syntax (& and ~)")

    safe = sub.add_parser("safety", help="screen a pattern for ReDoS")
    safe.add_argument("pattern")
    safe.add_argument("--no-empirical", action="store_true", help="structural analysis only")

    single = sub.add_parser("check", help="score a pattern against a task file")
    single.add_argument("pattern")
    single.add_argument("task", help="JSON file with positives/negatives/reference")

    batch = sub.add_parser("run", help="score predictions across a whole dataset")
    batch.add_argument("dataset", choices=_LOADERS, help="which loader to use")
    batch.add_argument("path", help="dataset file, or directory for deep-regex")
    batch.add_argument(
        "--predictions",
        help="JSON mapping task name -> pattern (or list of patterns), or a JSON array "
        "aligned with the tasks",
    )
    batch.add_argument(
        "--use-reference",
        action="store_true",
        help="score each task's own reference — a sanity check that the corpus, its "
        "dialect and its match semantics all load correctly",
    )
    batch.add_argument("--name", default="", help="label for the report")
    batch.add_argument("--k", type=int, nargs="+", default=[1], help="k values to report")
    batch.add_argument("--limit", type=int, help="score only the first N tasks")
    batch.add_argument("--workers", type=int, default=1, help="tasks to score concurrently")
    batch.add_argument("--timeout", type=float, default=1.0, help="seconds per match")
    batch.add_argument("--prompt", default="raw", help="regexeval only: raw or refined")
    batch.add_argument("--json", dest="as_json", help="also write the summary here as JSON")
    batch.add_argument("--quiet", action="store_true", help="no progress on stderr")

    cross = sub.add_parser(
        "crosscheck",
        help="check this engine against `re` over a file of patterns",
        description="Compare each pattern's automaton to what `re` matches, string "
        "by string. Reads JSON-lines LinguaFranca corpora and plain text files, one "
        "pattern per line. Exits non-zero if anything disagrees.",
    )
    cross.add_argument("path", help="corpus file, or - for stdin")
    cross.add_argument("--search", action="store_true", help="compare as re.search")
    cross.add_argument("--registry", help="LinguaFranca only: keep patterns used by e.g. pypi")
    cross.add_argument("--limit", type=int, help="stop after N patterns")
    cross.add_argument("--longest", type=int, default=3, help="longest string to compare")
    cross.add_argument("--quiet", action="store_true", help="no progress on stderr")

    return parser


def _load_dataset(args: argparse.Namespace) -> list[Task]:
    if args.dataset == "regexeval":
        return load_regexeval(args.path, prompt=args.prompt)
    if args.dataset == "deep-regex":
        return load_deep_regex(args.path)
    return load_tasks(args.path)


def _load_predictions(path: str):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, (dict, list)):
        raise ValueError(f"{path}: expected a JSON object or array of predictions")
    return payload


def _run(args: argparse.Namespace) -> int:
    tasks = _load_dataset(args)
    if args.limit is not None:
        tasks = tasks[: args.limit]

    if args.use_reference:
        missing = [task.name for task in tasks if task.reference is None]
        if missing:
            print(
                f"--use-reference needs every task to have one; {len(missing)} do not",
                file=sys.stderr,
            )
            return 2
        predictions: object = [task.reference for task in tasks]
    elif args.predictions:
        predictions = _load_predictions(args.predictions)
        if args.limit is not None and isinstance(predictions, dict):
            # --limit asks for a subset, so predictions for the tasks it cut
            # are expected rather than a mismatch. Without this, the harness's
            # unknown-name check makes --limit unusable with a full
            # predictions file — which is exactly when you want it.
            wanted = {task.name for task in tasks}
            predictions = {
                name: value for name, value in predictions.items() if name in wanted
            }
    else:
        print("one of --predictions or --use-reference is required", file=sys.stderr)
        return 2

    def progress(done: int, total: int) -> None:
        if not args.quiet and (done % 25 == 0 or done == total):
            print(f"\r  scored {done}/{total}", end="", file=sys.stderr, flush=True)

    report = run_suite(
        tasks,
        predictions,  # type: ignore[arg-type]
        name=args.name,
        timeout=args.timeout,
        workers=args.workers,
        progress=progress,
    )
    if not args.quiet:
        print(file=sys.stderr)

    print(report.table(ks=args.k))
    if args.as_json:
        Path(args.as_json).write_text(
            json.dumps(report.summary(ks=args.k), indent=2), encoding="utf-8"
        )
    return 0


def _eq(args: argparse.Namespace) -> int:
    result = equivalent(
        args.left,
        args.right,
        semantics=Semantics.SEARCH if args.search else Semantics.FULLMATCH,
        dialect=Dialect.BRICS if args.brics else Dialect.PYTHON,
    )
    print(result.verdict.value)
    if result.witness is not None:
        print(f"  witness: {result.witness!r}")
    if result.reason:
        print(f"  {result.reason}")
    return 0 if result.verdict is Verdict.EQUIVALENT else 1


def _safety(args: argparse.Namespace) -> int:
    result = screen(args.pattern, empirical=not args.no_empirical)
    print(result.risk.value)
    if result.reason:
        print(f"  {result.reason}")
    if result.witness:
        print(f"  witness: {result.witness!r}")
    return 1 if result.risk.is_vulnerable else 0


def _check(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.task).read_text(encoding="utf-8"))
    task = task_from_dict(payload, where=args.task)
    result = check(args.pattern, task)
    print(f"{result.passed}/{result.total} ({result.accuracy:.0%})")
    if result.error:
        print(f"  error: {result.error}", file=sys.stderr)
    for text in result.false_negatives:
        print(f"  should match but does not: {text!r}")
    for text in result.false_positives:
        print(f"  should not match but does: {text!r}")
    return 0 if result.perfect else 1


def _read_patterns(args: argparse.Namespace) -> list[str]:
    """Patterns from a LinguaFranca corpus, or one per line from a text file.

    Which one is decided by the first non-empty line, so a corpus with a
    corrupt line partway through fails loudly instead of quietly falling back
    to reading its JSON as literal patterns.
    """
    if args.path == "-":
        lines = sys.stdin.read().splitlines()
    else:
        lines = Path(args.path).read_text(encoding="utf-8").splitlines()
        if _is_json_object(next((line for line in lines if line.strip()), "")):
            return load_linguafranca(args.path, registry=args.registry, limit=args.limit)

    patterns = [line for line in lines if line.strip()]
    return patterns[: args.limit] if args.limit else patterns


def _is_json_object(line: str) -> bool:
    try:
        return isinstance(json.loads(line), dict)
    except ValueError:
        return False


def _crosscheck(args: argparse.Namespace) -> int:
    semantics = Semantics.SEARCH if args.search else Semantics.FULLMATCH
    patterns = _read_patterns(args)

    agreed = compared = 0
    unchecked: dict[str, int] = {}
    disagreements: list[tuple[str, str]] = []

    for index, pattern in enumerate(patterns, start=1):
        if not args.quiet and index % 2000 == 0:
            print(f"  {index}/{len(patterns)}", file=sys.stderr, flush=True)
        result = crosscheck(pattern, semantics=semantics, longest=args.longest)
        if result.agreement is Agreement.AGREES:
            agreed += 1
            compared += result.compared
        elif result.agreement is Agreement.DISAGREES:
            disagreements.append((pattern, result.reason))
        else:
            reason = result.reason.split(" at position")[0]
            unchecked[reason] = unchecked.get(reason, 0) + 1

    total_unchecked = sum(unchecked.values())
    print(f"patterns          : {len(patterns)}")
    print(f"crosschecked      : {agreed + len(disagreements)}")
    print(f"strings compared  : {compared}")
    print(f"unchecked         : {total_unchecked}")
    print(f"DISAGREEMENTS     : {len(disagreements)}")
    for pattern, reason in disagreements[:20]:
        print(f"    {pattern[:60]!r}: {reason}")
    if unchecked and not args.quiet:
        print("\nwhy patterns went unchecked:")
        for reason, count in sorted(unchecked.items(), key=lambda kv: -kv[1])[:12]:
            print(f"  {count:7}  {reason[:64]}")
    return 1 if disagreements else 0


_COMMANDS = {
    "eq": _eq,
    "safety": _safety,
    "check": _check,
    "run": _run,
    "crosscheck": _crosscheck,
}


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return _COMMANDS[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
