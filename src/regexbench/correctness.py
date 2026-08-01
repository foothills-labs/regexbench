"""Scoring a pattern against worked examples."""

from __future__ import annotations

import re

from .equivalence import equivalent
from .execute import MatchTimeout, safe_fullmatch, safe_search
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

    match = safe_search if task.semantics is Semantics.SEARCH else safe_fullmatch
    passed = 0
    false_negatives: list[str] = []
    false_positives: list[str] = []

    for text in task.positives:
        try:
            matched = match(pattern, text, timeout=timeout)
        except (MatchTimeout, re.error):
            matched = False
        if matched:
            passed += 1
        else:
            false_negatives.append(text)

    for text in task.negatives:
        try:
            matched = match(pattern, text, timeout=timeout)
        except (MatchTimeout, re.error):
            matched = True
        if matched:
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
