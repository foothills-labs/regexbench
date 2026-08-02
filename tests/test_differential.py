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

from regexbench import Dialect, Semantics, Verdict, equivalent

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


@pytest.mark.parametrize("operator", ["&", "~"])
@pytest.mark.parametrize("seed", range(3))
def test_brics_operators_agree_with_re(operator: str, seed: int) -> None:
    """`&` and `~` have no `re` equivalent, but their operands do.

    So the ground truth is computed per operand — a string is in `(A)&(B)`
    exactly when `re` full-matches it against both, and in `~(A)` exactly when
    `re` does not match it at all — and the verdict is checked against that.
    """
    rng = random.Random(seed)
    checked = 0

    for _ in range(120):
        inner, other, candidate = (_random_pattern(rng) for _ in range(3))
        try:
            compiled_inner = re.compile(inner)
            compiled_other = re.compile(other)
            compiled_candidate = re.compile(candidate)
        except re.error:
            continue

        pattern = f"({inner})&({other})" if operator == "&" else f"~({inner})"

        def in_pattern(
            text: str,
            first: re.Pattern[str] = compiled_inner,
            second: re.Pattern[str] = compiled_other,
            op: str = operator,
        ) -> bool:
            if op == "&":
                return bool(first.fullmatch(text)) and bool(second.fullmatch(text))
            return not first.fullmatch(text)

        result = equivalent(pattern, candidate, dialect=Dialect.BRICS)
        if result.verdict in (Verdict.UNSUPPORTED, Verdict.UNDECIDABLE):
            continue
        checked += 1

        if result.verdict is Verdict.EQUIVALENT:
            for text in CORPUS:
                assert in_pattern(text) == bool(compiled_candidate.fullmatch(text)), (
                    f"claimed {pattern!r} == {candidate!r}, but they differ on {text!r}"
                )
        else:
            witness = result.witness
            assert witness is not None
            assert in_pattern(witness) != bool(compiled_candidate.fullmatch(witness)), (
                f"claimed {pattern!r} != {candidate!r} with witness {witness!r}, "
                f"but re says they agree on it"
            )

    assert checked > 40, "generator produced too few analyzable pairs to be meaningful"


BOUNDARY_ATOMS = [*ATOMS, r"\b", r"\B", r"\bx", r"x\b", r"\w"]
BOUNDARY_ALPHABET = "ab1 _"
BOUNDARY_CORPUS = [""] + [
    "".join(combo)
    for length in (1, 2, 3, 4)
    for combo in itertools.product(BOUNDARY_ALPHABET, repeat=length)
]


def _boundary_pattern(rng: random.Random, depth: int = 0) -> str:
    atom = rng.choice(BOUNDARY_ATOMS)
    # Quantifying a zero-width assertion is a syntax error in modern `re`.
    if atom not in (r"\b", r"\B"):
        atom += rng.choice(QUANTIFIERS)
    if depth < 2 and rng.random() < 0.5:
        atom += _boundary_pattern(rng, depth + 1)
    return atom


@pytest.mark.parametrize("semantics", [Semantics.FULLMATCH, Semantics.SEARCH])
@pytest.mark.parametrize("seed", range(3))
def test_word_boundaries_agree_with_re(seed: int, semantics: Semantics) -> None:
    """`\\b` is decided by splitting the alphabet and carrying one bit of state.

    Both halves of that are easy to get subtly wrong — and `\\B` has a Python
    quirk on the empty string — so every verdict is checked against `re`.
    """
    rng = random.Random(seed)
    method = "search" if semantics is Semantics.SEARCH else "fullmatch"
    checked = 0

    for _ in range(200):
        left, right = _boundary_pattern(rng), _boundary_pattern(rng)
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

        def matches(compiled: re.Pattern[str], text: str) -> bool:
            return getattr(compiled, method)(text) is not None

        if result.verdict is Verdict.EQUIVALENT:
            for text in BOUNDARY_CORPUS:
                assert matches(compiled_left, text) == matches(compiled_right, text), (
                    f"claimed {left!r} == {right!r} under {method}, "
                    f"but they differ on {text!r}"
                )
        else:
            witness = result.witness
            assert witness is not None
            assert matches(compiled_left, witness) != matches(compiled_right, witness), (
                f"claimed {left!r} != {right!r} with witness {witness!r}, "
                f"but re says they agree on it"
            )

    assert checked > 40, "generator produced too few analyzable pairs to be meaningful"


@pytest.mark.parametrize("seed", range(2))
def test_assertions_inside_brics_operators_agree_with_re(seed: int) -> None:
    """`&` and `~` determinize their operands, which bakes in a context.

    Python has no intersection, but lookaround expresses one *with* the right
    context: `P((A)&(B))` full-matches exactly what `P(?=(?:A)$)(?:B)` does,
    and the lookahead is evaluated at the real position in the real string —
    so a `\\b` inside A sees what the embedded sub-machine must see.
    """
    rng = random.Random(seed)
    prefixes = ["", "x", "a", " ", "x*"]
    checked = 0

    for _ in range(500):
        inner, other, candidate = (_boundary_pattern(rng) for _ in range(3))
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

        if result.verdict is Verdict.EQUIVALENT:
            for text in BOUNDARY_CORPUS:
                assert (compiled_reference.fullmatch(text) is not None) == (
                    compiled_candidate.fullmatch(text) is not None
                ), f"claimed {pattern!r} == {candidate!r}, but they differ on {text!r}"
        else:
            witness = result.witness
            assert witness is not None
            assert (compiled_reference.fullmatch(witness) is not None) != (
                compiled_candidate.fullmatch(witness) is not None
            ), (
                f"claimed {pattern!r} != {candidate!r} with witness {witness!r}, "
                f"but re says they agree on it"
            )

    assert checked > 40, "generator produced too few analyzable pairs to be meaningful"


UNICODE_ATOMS = ["a", "1", "_", "[ab]", "[^a]", r"\d", r"\D", r"\w", r"\W", r"\s",
                 r"\S", ".", "[0-9]", "[A-Za-z0-9_]", "a|1", "(a1)", r"\b", r"\B"]
# A digit, a letter, a space and a symbol from outside ASCII, alongside ASCII
# ones: `\d` and `[0-9]` differ only on characters like ٣, so a corpus of ASCII
# would call them equivalent and never notice.
UNICODE_ALPHABET = "a1_ !٣é\xa0€\n"
UNICODE_CORPUS = [""] + [
    "".join(combo)
    for length in (1, 2, 3)
    for combo in itertools.product(UNICODE_ALPHABET, repeat=length)
]


def _unicode_pattern(rng: random.Random, depth: int = 0) -> str:
    atom = rng.choice(UNICODE_ATOMS)
    if atom not in (r"\b", r"\B"):
        atom += rng.choice(QUANTIFIERS)
    if depth < 2 and rng.random() < 0.5:
        atom += _unicode_pattern(rng, depth + 1)
    return atom


@pytest.mark.parametrize("semantics", [Semantics.FULLMATCH, Semantics.SEARCH])
@pytest.mark.parametrize("seed", range(2))
def test_shorthand_classes_agree_with_re_over_unicode(seed: int, semantics: Semantics) -> None:
    """`\\d`, `\\w` and `\\s` are Unicode-aware in `re`, and must be here too.

    The enumerated ASCII members keep witnesses readable; the rest of Unicode
    is covered by class, since it cannot be enumerated and must not be dropped.
    This corpus also carries a newline, which is what catches a search
    reduction built from `.` — `re.search` crosses newlines and `.` does not.
    """
    rng = random.Random(seed)
    method = "search" if semantics is Semantics.SEARCH else "fullmatch"
    checked = 0

    for _ in range(250):
        left, right = _unicode_pattern(rng), _unicode_pattern(rng)
        try:
            compiled_left, compiled_right = re.compile(left), re.compile(right)
        except re.error:
            continue

        result = equivalent(left, right, semantics=semantics)
        if result.verdict in (Verdict.UNSUPPORTED, Verdict.UNDECIDABLE):
            continue
        checked += 1

        def matches(compiled: re.Pattern[str], text: str) -> bool:
            return getattr(compiled, method)(text) is not None

        if result.verdict is Verdict.EQUIVALENT:
            for text in UNICODE_CORPUS:
                assert matches(compiled_left, text) == matches(compiled_right, text), (
                    f"claimed {left!r} == {right!r} under {method}, "
                    f"but they differ on {text!r}"
                )
        else:
            witness = result.witness
            assert witness is not None
            assert matches(compiled_left, witness) != matches(compiled_right, witness), (
                f"claimed {left!r} != {right!r} with witness {witness!r}, "
                f"but re says they agree on it"
            )

    assert checked > 40, "generator produced too few analyzable pairs to be meaningful"
