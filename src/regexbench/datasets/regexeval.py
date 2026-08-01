"""Re(gEx|DoS)Eval — 762 regex prompts written by real users, with tests.

From "Re(gEx|DoS)Eval: Evaluating Generated Regular Expressions and their
Proneness to DoS Attacks" (ICSE-NIER 2024). Each record carries a reference
expression, the prompt as a user actually wrote it, a refined prompt with
worked examples appended, and hand-built matching and non-matching strings.

The file is ``RegexEval.json`` from the ``DatasetCollection`` directory of
https://github.com/s2e-lab/RegexEval — a JSON list of records. It is not
redistributed here; download it and pass the path.

**These tasks are search-semantics.** Measured across all 762 records, the
reference expressions pass 100% of their own tests under ``re.search`` and
94.0% under ``re.fullmatch``. Loading them full-match would score 46 gold
patterns as failing the tests they were written for.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..types import Semantics, Task

__all__ = ["load_regexeval", "PROMPT_STYLES"]

PROMPT_STYLES = ("raw", "refined")

_REQUIRED = ("expression", "raw_prompt", "refined_prompt", "matches", "non_matches", "id")


def load_regexeval(path: str | Path, *, prompt: str = "raw") -> list[Task]:
    """Load RegexEval from `path`.

    `prompt` selects which wording becomes ``Task.prompt``: ``"raw"`` is what
    the user typed, ``"refined"`` appends worked examples to it. The two are
    different experiments — the paper reports both — so neither is treated as
    the default reading of the other.
    """
    if prompt not in PROMPT_STYLES:
        raise ValueError(f"prompt must be one of {PROMPT_STYLES}, got {prompt!r}")

    records = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError(f"{path}: expected a JSON list of records, got {type(records).__name__}")

    tasks = []
    for position, record in enumerate(records):
        missing = [key for key in _REQUIRED if key not in record]
        if missing:
            raise ValueError(f"{path}: record {position} is missing {', '.join(missing)}")
        tasks.append(
            Task(
                positives=list(record["matches"]),
                negatives=list(record["non_matches"]),
                prompt=record[f"{prompt}_prompt"],
                reference=record["expression"],
                name=f"regexeval/{record['id']}",
                semantics=Semantics.SEARCH,
            )
        )
    return tasks
