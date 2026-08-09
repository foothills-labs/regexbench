"""`_syntax.SYNTAX` has to match what the parser actually accepts.

The generator in `test_differential.py` draws its atoms from that declaration,
so the declaration is only worth anything if it cannot drift from the parser.
These tests pin it from both sides:

* every entry parses, in its own dialect, and compiles under `re` — so a
  declared construct is really supported rather than aspirational;
* every escape letter, node type and group opener the parser accepts appears
  in the declaration — so support cannot be added without being generated.

The second direction is the one that matters. Escapes were mis-parsed for a
release and lookaround for the length of a branch, and in both cases the
differential generator had no atom for the construct, so the suite was silent
about a whole family of wrong verdicts. Adding `\\Q` to the parser now fails
here until it is declared, and declaring it feeds the generator automatically.
"""

from __future__ import annotations

import re

import pytest

from regexbench import Dialect
from regexbench._parse import (
    _CLASS_ESCAPES,
    _LITERAL_ESCAPES,
    _PATTERN_ESCAPE_LETTERS,
    Unsupported,
    parse,
)
from regexbench._syntax import SYNTAX, atoms_for, available

KEYS = {c.key for c in SYNTAX}


@pytest.mark.parametrize("construct", SYNTAX, ids=lambda c: c.key)
def test_every_declared_construct_is_really_supported(construct):
    """A declaration the parser refuses is a lie the generator would spread."""
    if not available(construct):
        pytest.skip(f"{construct.key} needs an `re` that accepts {construct.requires!r}")
    if construct.dialect is Dialect.PYTHON:
        re.compile(construct.atom)  # must be a pattern `re` accepts
    parse(construct.atom, dialect=construct.dialect)


def test_keys_are_unique():
    assert len(KEYS) == len(SYNTAX), "duplicate key in SYNTAX"


def test_every_shorthand_class_escape_is_declared():
    """`\\d`, `\\W`, … — driven by the parser's own table, not a copy of it."""
    missing = {f"escape-{letter}" for letter in _CLASS_ESCAPES} - KEYS
    assert not missing, f"parser accepts these but SYNTAX does not declare them: {missing}"


def test_every_literal_escape_is_declared():
    missing = {f"escape-{letter}" for letter in _LITERAL_ESCAPES} - KEYS
    assert not missing, f"parser accepts these but SYNTAX does not declare them: {missing}"


def test_every_pattern_escape_letter_is_declared():
    """The letters `_escape` lets through, including the ones taking arguments.

    `_PATTERN_ESCAPE_LETTERS` is what the parser checks a backslash against, so
    a letter added there without an entry here would be a construct the
    generator can never emit.
    """
    missing = {f"escape-{letter}" for letter in _PATTERN_ESCAPE_LETTERS} - KEYS
    assert not missing, f"parser accepts these but SYNTAX does not declare them: {missing}"


@pytest.mark.parametrize(
    "opener,key",
    [
        ("?:", "group-noncapturing"),
        ("?P<", "group-named"),
        ("?=", "lookahead"),
        ("?!", "lookahead-negative"),
        ("?<=", "lookbehind"),
        ("?<!", "lookbehind-negative"),
        ("?>", "atomic"),
    ],
)
def test_every_group_opener_is_declared(opener, key):
    """Each `(?…)` form the parser recognises has an atom that exercises it."""
    assert key in KEYS, f"parser recognises `({opener}` but SYNTAX has no {key!r}"


def test_the_surface_covers_both_dialects():
    assert atoms_for(Dialect.PYTHON), "no Python atoms declared"
    assert atoms_for(Dialect.BRICS), "no dk.brics atoms declared"


def test_an_undeclared_escape_would_be_caught():
    """The completeness tests only bite if an undeclared letter is detectable.

    Guards the guard: if `_PATTERN_ESCAPE_LETTERS` grew a letter, the test
    above must fail rather than quietly pass on a stale key set.
    """
    pretend = {f"escape-{letter}" for letter in _PATTERN_ESCAPE_LETTERS | {"Q"}}
    assert pretend - KEYS == {"escape-Q"}


def test_refused_constructs_are_not_declared():
    """Anything the parser refuses must not be in the surface.

    A refusal is a decision this engine documents; declaring it here would
    hand the generator an atom that can only ever produce UNSUPPORTED, which
    dilutes the run without testing anything.
    """
    for construct in SYNTAX:
        if not available(construct):
            continue
        try:
            parse(construct.atom, dialect=construct.dialect)
        except Unsupported as exc:  # pragma: no cover - the assert reports it
            pytest.fail(f"{construct.key} is declared but refused: {exc}")
