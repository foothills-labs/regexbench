"""Regexes nobody curated, checked against `re` string by string.

Every other test in this suite exercises a construct someone chose. That is
exactly the weakness: each wrong-answer family this engine has shipped was a
construct nobody had thought to write a test for, and the differential
generator can only emit what its atom list declares.

So the suite also runs patterns from three corpora of regexes people actually
wrote — PyPI packages, Stack Overflow posts, regexlib.com — and asks the
sharper question: not "do two verdicts agree" but "does this automaton match
what `re` matches". Six wrong-answer bugs were found this way, and the first
ten patterns in the fixture are the ones that found them.

`tests/data/README.md` says where the fixture comes from. The full corpora are
half a million patterns and are not checked in; `REGEXBENCH_CORPUS` points this
at one when you want the whole thing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from regexbench import Agreement, Semantics, crosscheck
from regexbench.datasets import load_linguafranca

FIXTURE = Path(__file__).parent / "data" / "real_world_patterns.txt"
PATTERNS = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]

# The ten patterns that exposed the six bugs, kept at the head of the file
# so a failure names the bug rather than an anonymous line number.
REGRESSIONS = PATTERNS[:10]


def _sweep(patterns, semantics):
    """Crosscheck all of them, returning the disagreements and the coverage."""
    disagreements = []
    crosschecked = 0
    for pattern in patterns:
        result = crosscheck(pattern, semantics=semantics)
        if result.agreement is Agreement.DISAGREES:
            disagreements.append(f"{pattern!r}: {result.reason}")
        elif result.agreement is Agreement.AGREES:
            crosschecked += 1
    return disagreements, crosschecked


@pytest.mark.parametrize("semantics", [Semantics.FULLMATCH, Semantics.SEARCH])
def test_real_world_patterns_agree_with_re(semantics):
    disagreements, _ = _sweep(PATTERNS, semantics)
    assert not disagreements, "\n".join(disagreements)


@pytest.mark.parametrize("semantics", [Semantics.FULLMATCH, Semantics.SEARCH])
def test_enough_of_the_fixture_is_actually_crosschecked(semantics):
    """A sweep that refuses everything passes the test above and means nothing.

    The fixture was sampled at 90 crosscheckable patterns per corpus, so a
    change that pushes coverage far below that is refusing real-world syntax
    it used to decide — worth failing on and looking at, even though a
    refusal is never a wrong answer.
    """
    _, crosschecked = _sweep(PATTERNS, semantics)
    assert crosschecked >= 220, (
        f"only {crosschecked} of {len(PATTERNS)} patterns were crosschecked under "
        f"{semantics.name}; it was 272 under FULLMATCH and 249 under SEARCH when "
        f"the fixture was sampled"
    )


@pytest.mark.parametrize("pattern", REGRESSIONS, ids=range(len(REGRESSIONS)))
@pytest.mark.parametrize("semantics", [Semantics.FULLMATCH, Semantics.SEARCH])
def test_the_patterns_that_found_the_bugs(pattern, semantics):
    """Named individually, because each stands for a wrong-answer family.

    In order: `$` folded as plain end-of-string, the identity-keyed memo that
    could read another node's answer, an anchor dropped from a region a `$`
    collapsed, five nestings of one assertion inside another's body, and two
    boundaries at the right edge of a lookbehind window.
    """
    result = crosscheck(pattern, semantics=semantics)
    assert result.agreement is not Agreement.DISAGREES, result.reason


@pytest.mark.skipif(
    not os.environ.get("REGEXBENCH_CORPUS"),
    reason="set REGEXBENCH_CORPUS to a LinguaFranca corpus file to sweep it",
)
@pytest.mark.parametrize("semantics", [Semantics.FULLMATCH, Semantics.SEARCH])
def test_a_downloaded_corpus_agrees_with_re(semantics):
    """The whole thing, when you have it — the sweep the fixture stands in for.

    Bounded by `REGEXBENCH_CORPUS_LIMIT` because the production corpus is
    537,806 patterns and the Stack Overflow one 495,135.
    """
    limit = int(os.environ.get("REGEXBENCH_CORPUS_LIMIT", "5000"))
    patterns = load_linguafranca(
        os.environ["REGEXBENCH_CORPUS"],
        registry=os.environ.get("REGEXBENCH_CORPUS_REGISTRY") or None,
        limit=limit,
    )
    assert patterns, "corpus loaded no patterns"
    disagreements, crosschecked = _sweep(patterns, semantics)
    assert not disagreements, "\n".join(disagreements[:20])
    assert crosschecked, "nothing in the corpus could be crosschecked"
