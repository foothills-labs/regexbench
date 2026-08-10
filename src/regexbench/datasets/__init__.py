"""Loaders for the published regex benchmarks.

Scores are only comparable when they are computed on the same problems, so
these read the datasets the regex-generation literature actually reports on:

* :func:`load_regexeval` — Re(gEx|DoS)Eval, real user prompts with worked
  examples, scored by search.
* :func:`load_deep_regex` — KB13 and NL-RX, natural language paired with a
  gold pattern in dk.brics syntax and no examples, scored by equivalence.
* :func:`load_tasks` — your own problems, in a format you control.

One loader here is not a benchmark:

* :func:`load_linguafranca` — half a million patterns people actually wrote,
  with no prompt and no reference. Nothing to score a model against; what they
  are for is checking this engine against `re` on syntax nobody curated.

No dataset is redistributed with this package. Each loader takes the path to
files you download yourself, which keeps their licensing theirs and keeps
`regexbench` dependency-free and small.

Loaders do not filter. A record whose pattern this engine cannot represent is
still returned, and surfaces as an UNSUPPORTED verdict when it is scored,
because a corpus quietly reduced to its easy half produces a number that
cannot be compared to anything.
"""

from .custom import load_tasks, task_from_dict
from .deep_regex import load_deep_regex
from .linguafranca import load_linguafranca
from .regexeval import PROMPT_STYLES, load_regexeval

__all__ = [
    "PROMPT_STYLES",
    "load_deep_regex",
    "load_linguafranca",
    "load_regexeval",
    "load_tasks",
    "task_from_dict",
]
