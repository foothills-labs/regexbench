"""regexbench — evaluate a regex the way a benchmark should.

Three things a generated pattern has to survive, none of which string
comparison can tell you:

    >>> from regexbench import equivalent, screen, check, Task
    >>> bool(equivalent(r"[0-9]+", r"\\d+"))          # same language
    True
    >>> screen(r"(a+)+$").risk.is_vulnerable          # ReDoS
    True
    >>> check(r"\\d{3}", Task(positives=["123"], negatives=["12", "abc"])).perfect
    True

Built for scoring models that write regex, where exact-match against a
reference is the wrong metric and unsafe output is a real cost.
"""

from ._parse import NonRegular, Unsupported
from .correctness import check, evaluate
from .equivalence import equivalent, is_regular
from .execute import MatchTimeout, safe_fullmatch, safe_search
from .safety import attack_strings, screen
from .types import (
    CorrectnessResult,
    EquivalenceResult,
    Report,
    Risk,
    SafetyResult,
    Semantics,
    Task,
    Verdict,
)

__version__ = "0.1.0"

__all__ = [
    "CorrectnessResult",
    "EquivalenceResult",
    "MatchTimeout",
    "NonRegular",
    "Report",
    "Risk",
    "SafetyResult",
    "Semantics",
    "Task",
    "Unsupported",
    "Verdict",
    "__version__",
    "attack_strings",
    "check",
    "equivalent",
    "evaluate",
    "is_regular",
    "safe_fullmatch",
    "safe_search",
    "screen",
]
