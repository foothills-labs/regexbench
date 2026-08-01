"""Result types shared across the package."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class Verdict(enum.Enum):
    """The answer to "are these two patterns the same language?"

    UNDECIDABLE is not a failure to compute — it is the correct answer when a
    pattern uses backreferences or lookaround, which make the language
    non-regular and equivalence formally undecidable. Reporting a guess there
    would be worse than reporting nothing.
    """

    EQUIVALENT = "equivalent"
    DIFFERENT = "different"
    UNDECIDABLE = "undecidable"
    UNSUPPORTED = "unsupported"


class Risk(enum.Enum):
    """ReDoS exposure."""

    SAFE = "safe"
    POLYNOMIAL = "polynomial"
    EXPONENTIAL = "exponential"

    @property
    def is_vulnerable(self) -> bool:
        return self is not Risk.SAFE


@dataclass(frozen=True)
class EquivalenceResult:
    verdict: Verdict
    witness: str | None = None
    reason: str = ""

    def __bool__(self) -> bool:
        return self.verdict is Verdict.EQUIVALENT


@dataclass(frozen=True)
class SafetyResult:
    risk: Risk
    reason: str = ""
    witness: str | None = None

    def __bool__(self) -> bool:
        return self.risk is Risk.SAFE


@dataclass(frozen=True)
class CorrectnessResult:
    passed: int
    total: int
    false_negatives: list[str] = field(default_factory=list)
    false_positives: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def accuracy(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def perfect(self) -> bool:
        return self.error is None and self.total > 0 and self.passed == self.total

    def __bool__(self) -> bool:
        return self.perfect


@dataclass(frozen=True)
class Task:
    """One regex problem: what to match, and what not to."""

    positives: list[str]
    negatives: list[str]
    prompt: str = ""
    reference: str | None = None
    name: str = ""

    def __post_init__(self) -> None:
        if not self.positives and not self.negatives:
            raise ValueError("a task needs at least one positive or negative example")


@dataclass(frozen=True)
class Report:
    """Everything known about one candidate pattern."""

    pattern: str
    correctness: CorrectnessResult
    safety: SafetyResult
    equivalence: EquivalenceResult | None = None

    @property
    def usable(self) -> bool:
        """Correct on every example and not a ReDoS liability."""
        return self.correctness.perfect and not self.safety.risk.is_vulnerable
