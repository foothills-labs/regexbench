"""Result types shared across the package."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class Verdict(enum.Enum):
    """The answer to "are these two patterns the same language?"

    UNDECIDABLE is not a failure to compute — it is the correct answer when a
    pattern uses backreferences, which make the language non-regular and
    equivalence formally undecidable. Reporting a guess there would be worse
    than reporting nothing.
    """

    EQUIVALENT = "equivalent"
    DIFFERENT = "different"
    UNDECIDABLE = "undecidable"
    UNSUPPORTED = "unsupported"


class Semantics(enum.Enum):
    """What it means for a pattern to "match" a string.

    Benchmarks disagree on this, and getting it wrong silently invalidates
    every score. Re(gEx|DoS)Eval expects ``re.search`` — its reference
    patterns pass 100% of their own tests under SEARCH and only 94% under
    FULLMATCH. The KB13 and NL-RX corpora are anchored line matchers and want
    FULLMATCH.
    """

    FULLMATCH = "fullmatch"
    SEARCH = "search"


class Dialect(enum.Enum):
    """Which regex language a pattern is written in.

    The natural-language-to-regex corpora (KB13, NL-RX) are written in
    ``dk.brics.automaton`` syntax — the language the field's own DFA-equality
    tooling reads — which adds intersection (``&``) and complement (``~``) and
    has no anchors. Those same characters are ordinary literals in Python, and
    ``^``/``$`` are literals in dk.brics, so reading one dialect as the other
    changes the language without raising anything. It has to be stated.

    BRICS patterns are specifications, not runnable Python: ``re`` will
    happily compile ``(a)&(b)`` as a five-character literal.
    """

    PYTHON = "python"
    BRICS = "brics"


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


class Agreement(enum.Enum):
    """Whether this engine's automaton matches what Python's `re` matches.

    UNCHECKED is the ordinary outcome for a pattern the engine refuses or
    `re` will not compile. It is not a failure and not a pass: nothing was
    compared, which is a different statement from "the two agree".
    """

    AGREES = "agrees"
    DISAGREES = "disagrees"
    UNCHECKED = "unchecked"


@dataclass(frozen=True)
class CrosscheckResult:
    """One pattern's automaton weighed against `re`, string by string."""

    agreement: Agreement
    #: The string the two answer differently on, when they do.
    witness: str | None = None
    #: Why nothing was compared, when nothing was.
    reason: str = ""
    #: How many strings were compared.
    compared: int = 0
    #: The alphabet those strings were drawn from.
    alphabet: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        """True unless a disagreement was actually found.

        An UNCHECKED pattern is not evidence against the engine, so it does
        not fail a sweep — count the agreements if you want coverage.
        """
        return self.agreement is not Agreement.DISAGREES


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
    """One regex problem: what to match, and what not to.

    A task needs *something* to score against — worked examples, a reference
    pattern, or both. KB13 and NL-RX ship a gold pattern and no examples at
    all, so an example-free task is legitimate and scored by equivalence
    alone.

    `semantics` and `dialect` describe the problem, not just the reference:
    a candidate is read in the same dialect and matched under the same
    semantics as the pattern it is being compared against. Scoring a candidate
    against a BRICS task means claiming the candidate is BRICS too — which is
    the right claim when a model was asked to answer in that corpus's own
    notation, and the wrong one otherwise.
    """

    positives: list[str] = field(default_factory=list)
    negatives: list[str] = field(default_factory=list)
    prompt: str = ""
    reference: str | None = None
    name: str = ""
    semantics: Semantics = Semantics.FULLMATCH
    dialect: Dialect = Dialect.PYTHON

    def __post_init__(self) -> None:
        if not self.positives and not self.negatives and self.reference is None:
            raise ValueError("a task needs at least one example or a reference pattern")

    @property
    def has_examples(self) -> bool:
        return bool(self.positives or self.negatives)


@dataclass(frozen=True)
class Report:
    """Everything known about one candidate pattern."""

    pattern: str
    correctness: CorrectnessResult
    safety: SafetyResult
    equivalence: EquivalenceResult | None = None

    @property
    def usable(self) -> bool:
        """Right, and not a ReDoS liability.

        The strongest available evidence wins, and a proven difference is
        stronger than a passing example. `#[0-9a-f]{6}` passes a hex-colour
        task whose examples happen to be lowercase, and is still the wrong
        pattern — equivalence says so, and it is not overruled by examples
        that did not happen to ask. Treating a handful of examples as the last
        word is the failure mode this package exists to avoid.

        So: never vulnerable, never proven DIFFERENT from the reference, and
        then perfect on whatever examples exist. UNSUPPORTED and UNDECIDABLE
        do not count against a pattern — they mean the engine could not
        answer, not that the pattern is wrong — but with no examples to fall
        back on they leave nothing establishing that it works.
        """
        if self.safety.risk.is_vulnerable:
            return False
        if self.equivalence is not None and self.equivalence.verdict is Verdict.DIFFERENT:
            return False
        if self.correctness.total:
            return self.correctness.perfect
        if self.equivalence is not None:
            return self.equivalence.verdict is Verdict.EQUIVALENT
        return False
