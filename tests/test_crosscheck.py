"""`crosscheck` — this engine's automaton against `re`, one pattern at a time.

The tests that use it in anger are in `test_real_world.py`, which runs it over
patterns nobody curated. These pin the function itself: that it separates a
disagreement from a refusal, that it reports what it compared, and that the
alphabet it builds strings from contains the two characters the bugs it exists
to find both needed.
"""

from __future__ import annotations

import pytest

from regexbench import Agreement, Semantics, crosscheck
from regexbench._automata import build_dfa


@pytest.mark.parametrize(
    "pattern", [r"(ab)+", r"[0-9]{2}", r"a|b", r"\d\w\s", r"(?=a)ab", r"^a$", r"a*?b"]
)
@pytest.mark.parametrize("semantics", [Semantics.FULLMATCH, Semantics.SEARCH])
def test_a_supported_pattern_agrees(pattern, semantics):
    result = crosscheck(pattern, semantics=semantics)
    assert result.agreement is Agreement.AGREES, result.reason
    assert result.compared > 0
    assert bool(result) is True


@pytest.mark.parametrize(
    "pattern,fragment",
    [
        (r"(a)\1", "backreferences"),
        (r"[\D0-9]", "negated class escape"),
        (r"a$\n", "newline"),
        (r"a**", "`re` refuses it"),
        (r"(?<=a+)b", "`re` refuses it"),
    ],
)
def test_a_refusal_is_unchecked_and_says_why(pattern, fragment):
    """UNCHECKED is not a pass. Nothing was compared, and the reason says so."""
    result = crosscheck(pattern)
    assert result.agreement is Agreement.UNCHECKED
    assert fragment in result.reason
    assert result.compared == 0
    assert bool(result) is True  # not evidence against the engine


def test_the_alphabet_carries_a_newline_and_a_character_the_pattern_never_names():
    r"""Both are load-bearing, and both were added after a bug got past.

    A pattern's own literals cannot separate `\d` from `[0-9]` — that takes a
    character outside the pattern, which is the sentinel path through the
    automaton. And nothing separates `b$` from `b` under a search except a
    subject ending in a newline, because Python's `$` matches before one.
    """
    result = crosscheck(r"b")
    assert "\n" in result.alphabet
    assert set(result.alphabet) - {"b", "\n"}, "no character outside the pattern"


def test_a_longer_string_space_compares_more():
    assert crosscheck("a", longest=1).compared < crosscheck("a", longest=3).compared


def test_a_disagreement_reports_a_witness_re_can_be_checked_on(monkeypatch):
    """The one path the corpora exercise and a green suite never should.

    Faked by making the automaton answer the opposite of itself, because the
    engine is not currently wrong about anything — which is the point, and
    also why this path would otherwise go untested until the next bug.
    """
    def lying_build(node, alphabet, **kwargs):
        dfa = build_dfa(node, alphabet, **kwargs)
        original = dfa.accepts
        object.__setattr__(dfa, "accepts", lambda text: not original(text))
        return dfa

    monkeypatch.setattr("regexbench.agreement.build_dfa", lying_build)
    result = crosscheck(r"ab")
    assert result.agreement is Agreement.DISAGREES
    assert result.witness is not None
    assert bool(result) is False
    assert "says otherwise" in result.reason
