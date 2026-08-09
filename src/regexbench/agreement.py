"""Weigh this engine's automaton against Python's own engine, on one pattern.

Everything else in this package compares two *patterns*. This compares one
pattern's automaton to `re`, string by string, which is a different and
sharper question — and the one that finds bugs.

A verdict about a pair only goes wrong when the two patterns go wrong in
*different* ways, so a rule this engine applies uniformly cancels out of it
entirely. Folding `$` as plain end-of-string, when Python's `$` also matches
before a string-final newline, produced exactly one bad verdict in 21,000
generated pairs — and a disagreement on the first pattern ending in `$` once
membership was compared directly. Three more wrong-answer bugs were found the
same way, by running this over real-world corpora.

So it is public. If you are deciding whether to trust a verdict from this
package, this is the check to run, and `regexbench crosscheck` runs it over a
file of patterns.

The string space is small on purpose. A pattern's own literals, plus one
character it never names — that last one is the sentinel path through the
automaton, where `\\d` and `[0-9]` first differ — plus a newline, because `$`
treats a string-final one unlike any other character. Every string up to three
characters over that alphabet is then compared. Short strings find these bugs:
of the four above, the longest witness was two characters.
"""

from __future__ import annotations

import itertools
import re
import warnings

from ._automata import build_dfa
from ._parse import NonRegular, Unsupported, parse
from .types import Agreement, CrosscheckResult, Dialect, Semantics

__all__ = ["crosscheck"]

#: Characters tried as "one this pattern never names", in order. Any character
#: outside the pattern would do; these keep a witness readable.
_OUTSIDERS = "z9 _-٢"

#: How many of the pattern's own literals to draw on. Every character added
#: multiplies the string space by itself three times over, and the bugs this
#: finds do not need a wide alphabet — they need the right few characters.
_MAX_LITERALS = 4


def crosscheck(
    pattern: str,
    *,
    semantics: Semantics = Semantics.FULLMATCH,
    longest: int = 3,
) -> CrosscheckResult:
    """Compare `pattern`'s automaton against `re` over a small string space.

    Returns AGREES when every string was answered the same way, DISAGREES with
    the witness when one was not, and UNCHECKED with a reason when there was
    nothing to compare — because `re` will not compile the pattern, or because
    this engine states a refusal for it.

    Only Python-dialect patterns can be crosschecked: `re` is the ground truth
    here, and it reads a dk.brics pattern as a different language rather than
    refusing it, so there is nothing to compare against.

    Termination is bounded by the determinization budget rather than a clock.
    A handful of patterns in half a million take seconds; none run away.
    """
    try:
        with warnings.catch_warnings():
            # Real-world patterns trip `re`'s advisory warnings — a nested `[`
            # reads as a possible set intersection under a future syntax. That
            # is a note to the pattern's author, not to a sweep over corpora
            # somebody else wrote, and it compiles and matches either way.
            warnings.simplefilter("ignore", FutureWarning)
            compiled = re.compile(pattern)
    except re.error as exc:
        return CrosscheckResult(Agreement.UNCHECKED, reason=f"`re` refuses it: {exc}")

    try:
        node, literals = parse(pattern, semantics=semantics, dialect=Dialect.PYTHON)
        alphabet = _alphabet(literals)
        dfa = build_dfa(node, alphabet)
    except (Unsupported, NonRegular) as exc:
        return CrosscheckResult(Agreement.UNCHECKED, reason=str(exc))
    except RecursionError:
        return CrosscheckResult(Agreement.UNCHECKED, reason="pattern nests too deeply")

    matches = compiled.search if semantics is Semantics.SEARCH else compiled.fullmatch
    compared = 0
    for text in _texts(alphabet, longest):
        compared += 1
        if dfa.accepts(text) != (matches(text) is not None):
            return CrosscheckResult(
                Agreement.DISAGREES,
                witness=text,
                reason=(
                    f"automaton {'accepts' if dfa.accepts(text) else 'rejects'} "
                    f"{text!r}, `re`.{matches.__name__} says otherwise"
                ),
                compared=compared,
                alphabet=alphabet,
            )
    return CrosscheckResult(Agreement.AGREES, compared=compared, alphabet=alphabet)


def _alphabet(literals: frozenset[str]) -> tuple[str, ...]:
    """The characters to build strings from, for a pattern with `literals`."""
    chosen = set(sorted(literals)[:_MAX_LITERALS])
    chosen.add(next((c for c in _OUTSIDERS if c not in literals), "\x01"))
    chosen.add("\n")
    return tuple(sorted(chosen))


def _texts(alphabet: tuple[str, ...], longest: int) -> list[str]:
    return [""] + [
        "".join(combo)
        for length in range(1, longest + 1)
        for combo in itertools.product(alphabet, repeat=length)
    ]
