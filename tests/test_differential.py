"""Differential testing of the equivalence engine against Python's own engine.

The automata layer is the part most likely to be subtly wrong, and unit tests
only cover the cases someone thought of. This generates random patterns and
cross-checks every verdict against `re`:

* an EQUIVALENT verdict must hold for every string in the corpus
* a DIFFERENT verdict must come with a witness that `re` agrees separates them

Every scenario below is that same check with a different notion of "matches"
and a different corpus, so they share :func:`cross_check`. What varies is the
ground truth, and for two of them `re` has no direct equivalent:

* `&` and `~` do not exist in `re`, but their *operands* do — a string is in
  `(A)&(B)` exactly when `re` full-matches it against both.
* An assertion inside `&` or `~` depends on surrounding context, which
  per-operand truth loses. Lookaround recovers it: `P((A)&(B))` full-matches
  exactly what `P(?=(?:A)$)(?:B)` does, evaluated at the real position in the
  real string.

A seeded generator keeps failures reproducible.
"""

from __future__ import annotations

import itertools
import random
import re
from collections.abc import Callable, Sequence

import pytest

from regexbench import Dialect, EquivalenceResult, Semantics, Verdict, equivalent

Matcher = Callable[[str], bool]

ATOMS = ["a", "b", "c", "[ab]", "[^a]", r"\d", ".", "a|b", "(ab)", "[a-c]"]
QUANTIFIERS = ["", "*", "+", "?", "{2}", "{1,2}"]

# Zero-width assertions cannot be quantified: `\b*` is a syntax error in
# modern `re`.
ZERO_WIDTH = (r"\b", r"\B")


def corpus(alphabet: str, longest: int = 3) -> list[str]:
    """Every string up to `longest` characters over `alphabet`, plus ""."""
    return [""] + [
        "".join(combo)
        for length in range(1, longest + 1)
        for combo in itertools.product(alphabet, repeat=length)
    ]


def generator(atoms: Sequence[str]) -> Callable[[random.Random, int], str]:
    """A seeded random pattern builder drawing from `atoms`."""

    def build(rng: random.Random, depth: int = 0) -> str:
        pattern = rng.choice(atoms)
        if pattern not in ZERO_WIDTH:
            pattern += rng.choice(QUANTIFIERS)
        if depth < 2 and rng.random() < 0.5:
            pattern += build(rng, depth + 1)
        return pattern

    return build


def cross_check(
    result: EquivalenceResult,
    left: Matcher,
    right: Matcher,
    texts: Sequence[str],
    label: str,
) -> None:
    """Assert a verdict against ground truth.

    EQUIVALENT has to survive the whole corpus. DIFFERENT has to come with a
    witness the ground truth agrees separates the two — which is the stronger
    check, since it fails on a witness that is merely plausible.
    """
    if result.verdict is Verdict.EQUIVALENT:
        for text in texts:
            assert left(text) == right(text), (
                f"claimed {label} are equal, but they differ on {text!r}"
            )
        return

    witness = result.witness
    assert witness is not None, f"{label}: DIFFERENT without a witness"
    assert left(witness) != right(witness), (
        f"claimed {label} differ with witness {witness!r}, but ground truth agrees on it"
    )


def matcher(pattern: re.Pattern[str], method: str = "fullmatch") -> Matcher:
    return lambda text: getattr(pattern, method)(text) is not None


def method_for(semantics: Semantics) -> str:
    return "search" if semantics is Semantics.SEARCH else "fullmatch"


# --------------------------------------------------------------------------
# Plain full-match equivalence.

PLAIN = generator(ATOMS)
PLAIN_CORPUS = corpus("abc1.\n")


@pytest.mark.parametrize("seed", range(8))
def test_verdicts_agree_with_the_re_module(seed: int) -> None:
    rng = random.Random(seed)
    checked = 0

    for _ in range(150):
        left, right = PLAIN(rng), PLAIN(rng)
        try:
            compiled_left, compiled_right = re.compile(left), re.compile(right)
        except re.error:
            continue

        result = equivalent(left, right)
        if result.verdict in (Verdict.UNSUPPORTED, Verdict.UNDECIDABLE):
            continue
        checked += 1
        cross_check(
            result,
            matcher(compiled_left),
            matcher(compiled_right),
            PLAIN_CORPUS,
            f"{left!r} and {right!r}",
        )

    assert checked > 50, "generator produced too few analyzable pairs to be meaningful"


# --------------------------------------------------------------------------
# Search semantics, where anchors are what the reduction has to respect.

SEARCH_CORPUS = corpus("abc1", longest=4)


def _anchored(rng: random.Random, pattern: str) -> str:
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

    Easy to get subtly wrong — a leading `^` binds to the first branch of an
    alternation, not the whole pattern — so it is checked against `re.search`.
    """
    rng = random.Random(seed)
    checked = 0

    for _ in range(120):
        left = _anchored(rng, PLAIN(rng))
        right = _anchored(rng, PLAIN(rng))
        try:
            compiled_left, compiled_right = re.compile(left), re.compile(right)
        except re.error:
            continue

        result = equivalent(left, right, semantics=Semantics.SEARCH)
        if result.verdict in (Verdict.UNSUPPORTED, Verdict.UNDECIDABLE):
            continue
        checked += 1
        cross_check(
            result,
            matcher(compiled_left, "search"),
            matcher(compiled_right, "search"),
            SEARCH_CORPUS,
            f"searching {left!r} and {right!r}",
        )

    assert checked > 40, "generator produced too few analyzable pairs to be meaningful"


# --------------------------------------------------------------------------
# dk.brics operators, whose ground truth is computed per operand.


@pytest.mark.parametrize("operator", ["&", "~"])
@pytest.mark.parametrize("seed", range(3))
def test_brics_operators_agree_with_re(operator: str, seed: int) -> None:
    rng = random.Random(seed)
    checked = 0

    for _ in range(120):
        inner, other, candidate = PLAIN(rng), PLAIN(rng), PLAIN(rng)
        try:
            compiled_inner = re.compile(inner)
            compiled_other = re.compile(other)
            compiled_candidate = re.compile(candidate)
        except re.error:
            continue

        if operator == "&":
            pattern = f"({inner})&({other})"

            def in_pattern(
                text: str,
                first: re.Pattern[str] = compiled_inner,
                second: re.Pattern[str] = compiled_other,
            ) -> bool:
                return bool(first.fullmatch(text)) and bool(second.fullmatch(text))
        else:
            pattern = f"~({inner})"

            def in_pattern(
                text: str,
                first: re.Pattern[str] = compiled_inner,
                second: re.Pattern[str] = compiled_other,
            ) -> bool:
                return not first.fullmatch(text)

        result = equivalent(pattern, candidate, dialect=Dialect.BRICS)
        if result.verdict in (Verdict.UNSUPPORTED, Verdict.UNDECIDABLE):
            continue
        checked += 1
        cross_check(
            result,
            in_pattern,
            matcher(compiled_candidate),
            PLAIN_CORPUS,
            f"{pattern!r} and {candidate!r}",
        )

    assert checked > 40, "generator produced too few analyzable pairs to be meaningful"


# --------------------------------------------------------------------------
# Word boundaries.

BOUNDARY = generator([*ATOMS, r"\b", r"\B", r"\bx", r"x\b", r"\w"])
BOUNDARY_CORPUS = corpus("ab1 _", longest=4)


@pytest.mark.parametrize("semantics", [Semantics.FULLMATCH, Semantics.SEARCH])
@pytest.mark.parametrize("seed", range(3))
def test_word_boundaries_agree_with_re(seed: int, semantics: Semantics) -> None:
    """`\\b` is decided by splitting the alphabet and carrying one bit of state.

    Both halves are easy to get subtly wrong, and `\\B` has a Python quirk on
    the empty string, so every verdict is checked against `re`.
    """
    rng = random.Random(seed)
    method = method_for(semantics)
    checked = 0

    for _ in range(200):
        left, right = BOUNDARY(rng), BOUNDARY(rng)
        if "\\b" not in left + right and "\\B" not in left + right:
            continue
        try:
            compiled_left, compiled_right = re.compile(left), re.compile(right)
        except re.error:
            continue

        result = equivalent(left, right, semantics=semantics)
        if result.verdict in (Verdict.UNSUPPORTED, Verdict.UNDECIDABLE):
            continue
        checked += 1
        cross_check(
            result,
            matcher(compiled_left, method),
            matcher(compiled_right, method),
            BOUNDARY_CORPUS,
            f"{method} of {left!r} and {right!r}",
        )

    assert checked > 40, "generator produced too few analyzable pairs to be meaningful"


# --------------------------------------------------------------------------
# Assertions inside operators, where lookaround supplies the ground truth.


@pytest.mark.parametrize("seed", range(2))
def test_assertions_inside_brics_operators_agree_with_re(seed: int) -> None:
    """`&` and `~` determinize their operands, which bakes in a context.

    Lookaround expresses the same intersection *with* the right context, so a
    `\\b` inside the operand sees what the embedded sub-machine must see.
    """
    rng = random.Random(seed)
    prefixes = ["", "x", "a", " ", "x*"]
    checked = 0

    for _ in range(500):
        inner, other, candidate = BOUNDARY(rng), BOUNDARY(rng), BOUNDARY(rng)
        prefix = rng.choice(prefixes)
        if rng.random() < 0.5:
            pattern = f"{prefix}(({inner})&({other}))"
            reference = f"{prefix}(?=(?:{inner})$)(?:{other})"
        else:
            pattern = f"{prefix}(~({inner}))"
            reference = f"{prefix}(?!(?:{inner})$)(?:.*)"
        try:
            compiled_reference = re.compile(reference)
            compiled_candidate = re.compile(candidate)
        except re.error:
            continue

        result = equivalent(pattern, candidate, dialect=Dialect.BRICS)
        if result.verdict in (Verdict.UNSUPPORTED, Verdict.UNDECIDABLE):
            continue
        checked += 1
        cross_check(
            result,
            matcher(compiled_reference),
            matcher(compiled_candidate),
            BOUNDARY_CORPUS,
            f"{pattern!r} and {candidate!r}",
        )

    assert checked > 40, "generator produced too few analyzable pairs to be meaningful"


# --------------------------------------------------------------------------
# Unicode-aware shorthand classes.

UNICODE = generator(
    ["a", "1", "_", "[ab]", "[^a]", r"\d", r"\D", r"\w", r"\W", r"\s", r"\S",
     ".", "[0-9]", "[A-Za-z0-9_]", "a|1", "(a1)", r"\b", r"\B"]
)
# A digit, a letter, a space and a symbol from outside ASCII, alongside ASCII
# ones: `\d` and `[0-9]` differ only on characters like ٣, so an ASCII-only
# corpus would call them equivalent and never notice. The newline matters too —
# it is what catches a search reduction built from `.`, which excludes newlines
# where `re.search` crosses them.
UNICODE_CORPUS = corpus("a1_ !٣é\xa0€\n")


@pytest.mark.parametrize("semantics", [Semantics.FULLMATCH, Semantics.SEARCH])
@pytest.mark.parametrize("seed", range(2))
def test_shorthand_classes_agree_with_re_over_unicode(
    seed: int, semantics: Semantics
) -> None:
    """`\\d`, `\\w` and `\\s` are Unicode-aware in `re`, and must be here too."""
    rng = random.Random(seed)
    method = method_for(semantics)
    checked = 0

    for _ in range(250):
        left, right = UNICODE(rng), UNICODE(rng)
        try:
            compiled_left, compiled_right = re.compile(left), re.compile(right)
        except re.error:
            continue

        result = equivalent(left, right, semantics=semantics)
        if result.verdict in (Verdict.UNSUPPORTED, Verdict.UNDECIDABLE):
            continue
        checked += 1
        cross_check(
            result,
            matcher(compiled_left, method),
            matcher(compiled_right, method),
            UNICODE_CORPUS,
            f"{method} of {left!r} and {right!r}",
        )

    assert checked > 40, "generator produced too few analyzable pairs to be meaningful"
