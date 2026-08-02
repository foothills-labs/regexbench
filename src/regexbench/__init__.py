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

To score a model rather than a pattern, load a benchmark and run the whole
thing:

    >>> from regexbench import run                      # doctest: +SKIP
    >>> from regexbench.datasets import load_regexeval   # doctest: +SKIP
    >>> tasks = load_regexeval("RegexEval.json")         # doctest: +SKIP
    >>> print(run(tasks, predictions).table())           # doctest: +SKIP

`regexbench.datasets` carries the corpora this literature reports on, each
loaded with the match semantics and syntax dialect it actually uses, so a
number produced here can be put next to a published one.
"""

from ._parse import NonRegular, Unsupported
from .correctness import check, evaluate
from .equivalence import equivalent, is_regular
from .execute import MatchTimeout, match_many, safe_fullmatch, safe_search
from .harness import CandidateResult, SuiteReport, TaskResult, pass_at_k, run
from .safety import attack_strings, screen
from .types import (
    CorrectnessResult,
    Dialect,
    EquivalenceResult,
    Report,
    Risk,
    SafetyResult,
    Semantics,
    Task,
    Verdict,
)

__version__ = "0.2.0"

__all__ = [
    "CandidateResult",
    "CorrectnessResult",
    "Dialect",
    "EquivalenceResult",
    "MatchTimeout",
    "NonRegular",
    "Report",
    "Risk",
    "SafetyResult",
    "Semantics",
    "SuiteReport",
    "Task",
    "TaskResult",
    "Unsupported",
    "Verdict",
    "__version__",
    "attack_strings",
    "check",
    "equivalent",
    "evaluate",
    "is_regular",
    "match_many",
    "pass_at_k",
    "run",
    "safe_fullmatch",
    "safe_search",
    "screen",
]
