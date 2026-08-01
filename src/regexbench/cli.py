"""Command line interface."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .correctness import check
from .equivalence import equivalent
from .safety import screen
from .types import Task, Verdict


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

    safe = sub.add_parser("safety", help="screen a pattern for ReDoS")
    safe.add_argument("pattern")
    safe.add_argument("--no-empirical", action="store_true", help="structural analysis only")

    run = sub.add_parser("check", help="score a pattern against a task file")
    run.add_argument("pattern")
    run.add_argument("task", help="JSON file with positives/negatives/reference")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "eq":
        result = equivalent(args.left, args.right)
        print(result.verdict.value)
        if result.witness is not None:
            print(f"  witness: {result.witness!r}")
        if result.reason:
            print(f"  {result.reason}")
        return 0 if result.verdict is Verdict.EQUIVALENT else 1

    if args.command == "safety":
        result = screen(args.pattern, empirical=not args.no_empirical)
        print(result.risk.value)
        if result.reason:
            print(f"  {result.reason}")
        if result.witness:
            print(f"  witness: {result.witness!r}")
        return 1 if result.risk.is_vulnerable else 0

    with open(args.task, encoding="utf-8") as fh:
        payload = json.load(fh)
    task = Task(
        positives=payload.get("positives", []),
        negatives=payload.get("negatives", []),
        reference=payload.get("reference"),
        prompt=payload.get("prompt", ""),
        name=payload.get("name", ""),
    )
    result = check(args.pattern, task)
    print(f"{result.passed}/{result.total} ({result.accuracy:.0%})")
    if result.error:
        print(f"  error: {result.error}", file=sys.stderr)
    for text in result.false_negatives:
        print(f"  should match but does not: {text!r}")
    for text in result.false_positives:
        print(f"  should not match but does: {text!r}")
    return 0 if result.perfect else 1


if __name__ == "__main__":
    raise SystemExit(main())
