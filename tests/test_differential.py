"""Differential testing of the equivalence engine against Python's own engine.

The automata layer is the part most likely to be subtly wrong, and unit tests
only cover the cases someone thought of. This generates random patterns and
cross-checks every verdict against `re`:

* an EQUIVALENT verdict must hold for every string in the corpus
* a DIFFERENT verdict must come with a witness that `re` agrees separates them

A seeded generator keeps failures reproducible.
"""

from __future__ import annotations

import itertools
import random
import re

import pytest

from regexbench import Semantics, Verdict, equivalent

ATOMS = ["a", "b", "c", "[ab]", "[^a]", r"\d", ".", "a|b", "(ab)", "[a-c]"]
QUANTIFIERS = ["", "*", "+", "?", "{2}", "{1,2}"]
ALPHABET = "abc1.\n"

CORPUS = [""] + [
    "".join(combo)
    for length in (1, 2, 3)
    for combo in itertools.product(ALPHABET, repeat=length)
]


def _random_pattern(rng: random.Random, depth: int = 0) -> str:
    pattern = rng.choice(ATOMS) + rng.choice(QUANTIFIERS)
    if depth < 2 and rng.random() < 0.5:
        pattern += _random_pattern(rng, depth + 1)
    return pattern


@pytest.mark.parametrize("seed", range(8))
def test_verdicts_agree_with_the_re_module(seed: int) -> None:
    rng = random.Random(seed)
    checked = 0

    for _ in range(150):
        left, right = _random_pattern(rng), _random_pattern(rng)
        try:
            compiled_left, compiled_right = re.compile(left), re.compile(right)
        except re.error:
            continue

        result = equivalent(left, right)
        if result.verdict in (Verdict.UNSUPPORTED, Verdict.UNDECIDABLE):
            continue
        checked += 1

        same_on_corpus = all(
            (compiled_left.fullmatch(s) is not None)
            == (compiled_right.fullmatch(s) is not None)
            for s in CORPUS
        )

        if result.verdict is Verdict.EQUIVALENT:
            assert same_on_corpus, (
                f"claimed {left!r} == {right!r}, but they differ on the corpus"
            )
        else:
            witness = result.witness
            assert witness is not None
            in_left = compiled_left.fullmatch(witness) is not None
            in_right = compiled_right.fullmatch(witness) is not None
            assert in_left != in_right, (
                f"claimed {left!r} != {right!r} with witness {witness!r}, "
                f"but re says they agree on it"
            )

    assert checked > 50, "generator produced too few analyzable pairs to be meaningful"


SEARCH_CORPUS = [""] + [
    "".join(combo)
    for length in (1, 2, 3, 4)
    for combo in itertools.product("abc1", repeat=length)
]


def _anchored(rng: random.Random, pattern: str) -> str:
    """Sometimes anchor a pattern, since anchors are what search has to respect."""
    roll = rng.random()
    if roll < 0.15:
        return "^" + pattern
    if roll < 0.30:
        return pattern + "$"
    if roll < 0.40:
        return "^" + pattern + "$"
    return pattern


@pytest.mark.parametrize("seed", range(4))
def test_search_verdicts_agree_with_re_search(seed: int) -> None:
    """The search reduction rewrites `p` to `.*p.*` around whatever it anchors.

    That rewrite is easy to get subtly wrong — a leading `^` binds to the
    first branch of an alternation, not the whole pattern — so it is checked
    against `re.search` the same way full-match equivalence is.
    """
    rng = random.Random(seed)
    checked = 0

    for _ in range(120):
        left = _anchored(rng, _random_pattern(rng))
        right = _anchored(rng, _random_pattern(rng))
        try:
            compiled_left, compiled_right = re.compile(left), re.compile(right)
        except re.error:
            continue

        result = equivalent(left, right, semantics=Semantics.SEARCH)
        if result.verdict in (Verdict.UNSUPPORTED, Verdict.UNDECIDABLE):
            continue
        checked += 1

        if result.verdict is Verdict.EQUIVALENT:
            for text in SEARCH_CORPUS:
                assert (compiled_left.search(text) is not None) == (
                    compiled_right.search(text) is not None
                ), f"claimed searching {left!r} == {right!r}, but they differ on {text!r}"
        else:
            witness = result.witness
            assert witness is not None
            assert (compiled_left.search(witness) is not None) != (
                compiled_right.search(witness) is not None
            ), (
                f"claimed searching {left!r} != {right!r} with witness "
                f"{witness!r}, but re says they agree on it"
            )

    assert checked > 40, "generator produced too few analyzable pairs to be meaningful"
