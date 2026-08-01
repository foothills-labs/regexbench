"""Scoring a pattern against worked examples."""

from __future__ import annotations

import re

from .equivalence import equivalent
from .execute import match_many
from .safety import screen
from .types import CorrectnessResult, Dialect, Report, Risk, SafetyResult, Semantics, Task

__all__ = ["check", "evaluate"]


def check(pattern: str, task: Task, timeout: float = 1.0) -> CorrectnessResult:
    """Run `pattern` against a task's positive and negative examples.

    Matching follows the task's semantics — full match or search. A timeout
    counts as a failed example rather than an exception, so one pathological
    pattern cannot abort a benchmark sweep.
    """
    total = len(task.positives) + len(task.negatives)

    if task.dialect is Dialect.BRICS:
        # `re` would compile `(a)&(b)` happily, as a literal — running these
        # would produce confident nonsense rather than an error.
        if not total:
            return CorrectnessResult(0, 0)
        return CorrectnessResult(
            0, total, error="dk.brics patterns are specifications, not runnable by re"
        )

    try:
        re.compile(pattern)
    except re.error as exc:
        return CorrectnessResult(0, total, error=f"does not compile: {exc}")

    method = "search" if task.semantics is Semantics.SEARCH else "fullmatch"
    try:
        outcomes = match_many(
            pattern, task.positives + task.negatives, method=method, timeout=timeout
        )
    except re.error as exc:  # pragma: no cover - the compile check above catches these
        return CorrectnessResult(0, total, error=f"does not compile: {exc}")

    passed = 0
    false_negatives: list[str] = []
    false_positives: list[str] = []

    split = len(task.positives)
    for text, matched in zip(task.positives, outcomes[:split], strict=True):
        # A timeout is None, which counts as "did not match" here — the
        # example is failed rather than the sweep aborted.
        if matched:
            passed += 1
        else:
            false_negatives.append(text)

    for text, matched in zip(task.negatives, outcomes[split:], strict=True):
        # ...and as "did match" here, so a pattern that hangs is never scored
        # as correctly rejecting anything.
        if matched is None or matched:
            false_positives.append(text)
        else:
            passed += 1

    return CorrectnessResult(
        passed=passed,
        total=len(task.positives) + len(task.negatives),
        false_negatives=false_negatives,
        false_positives=false_positives,
    )


def evaluate(pattern: str, task: Task, timeout: float = 1.0) -> Report:
    """Full assessment: correctness, ReDoS safety, and equivalence if a
    reference pattern is available."""
    correctness = check(pattern, task, timeout=timeout)

    if task.dialect is Dialect.BRICS:
        # ReDoS is a property of backtracking engines. A dk.brics pattern is
        # destined for an automaton, where matching is linear by construction,
        # so screening it would be answering a question nobody asked.
        safety = SafetyResult(Risk.SAFE, reason="not screened: dk.brics patterns are not run by re")
    else:
        safety = screen(pattern)

    equivalence = (
        equivalent(pattern, task.reference, semantics=task.semantics, dialect=task.dialect)
        if task.reference
        else None
    )
    return Report(
        pattern=pattern,
        correctness=correctness,
        safety=safety,
        equivalence=equivalence,
    )
