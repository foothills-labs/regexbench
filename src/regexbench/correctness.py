"""Scoring a pattern against worked examples."""

from __future__ import annotations

import re

from .equivalence import equivalent
from .execute import MatchTimeout, safe_fullmatch
from .safety import screen
from .types import CorrectnessResult, Report, Task

__all__ = ["check", "evaluate"]


def check(pattern: str, task: Task, timeout: float = 1.0) -> CorrectnessResult:
    """Run `pattern` against a task's positive and negative examples.

    Uses full-match semantics. A timeout counts as a failed example rather
    than an exception, so one pathological pattern cannot abort a benchmark
    sweep.
    """
    try:
        re.compile(pattern)
    except re.error as exc:
        total = len(task.positives) + len(task.negatives)
        return CorrectnessResult(0, total, error=f"does not compile: {exc}")

    passed = 0
    false_negatives: list[str] = []
    false_positives: list[str] = []

    for text in task.positives:
        try:
            matched = safe_fullmatch(pattern, text, timeout=timeout)
        except (MatchTimeout, re.error):
            matched = False
        if matched:
            passed += 1
        else:
            false_negatives.append(text)

    for text in task.negatives:
        try:
            matched = safe_fullmatch(pattern, text, timeout=timeout)
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
    safety = screen(pattern)
    equivalence = equivalent(pattern, task.reference) if task.reference else None
    return Report(
        pattern=pattern,
        correctness=correctness,
        safety=safety,
        equivalence=equivalence,
    )
