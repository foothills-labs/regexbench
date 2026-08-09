"""The syntax this engine claims to support, as data.

One list, used three ways: the differential generator draws its atoms from it,
a test asserts the generator actually exercises every entry, and another test
asserts the list covers everything the parser's own tables accept. Adding a
construct to the parser without adding it here fails that last test; adding it
here without the generator reaching it fails the first.

That loop exists because its absence is how two families of wrong verdicts
shipped. Escapes were mis-parsed for a release, and lookaround for the length
of a branch, and in both cases the differential generator could not emit the
construct at all — so a suite that looked thorough was silent about it. A
generator whose alphabet is hand-maintained tests what someone remembered.

The grammar-fuzzing literature makes the same point from the other side: what
you want from a generator is *coverage of the grammar*, every production
exercised rather than a random walk that happens to visit some of them, and
that is only checkable when the grammar is written down somewhere the tests
can read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .types import Dialect

__all__ = ["Construct", "SYNTAX", "atoms_for", "available"]


@dataclass(frozen=True)
class Construct:
    """One piece of supported syntax, with a pattern that exercises it.

    `atom` is a fragment the generator can concatenate with others and still
    get something `re` compiles. `key` is what the completeness test matches
    against the parser's tables.
    """

    key: str
    atom: str
    dialect: Dialect = Dialect.PYTHON
    #: Set when the construct is only decided under full-match semantics.
    fullmatch_only: bool = False
    #: A pattern whose truth this construct depends on, probed against the
    #: running `re`. `None` means every supported interpreter accepts it.
    #: Possessive quantifiers and atomic groups arrived in CPython 3.11, and
    #: the parser refuses them where `re` cannot compile them, so the surface
    #: has to know that too rather than assert a claim the interpreter denies.
    requires: str | None = None


SYNTAX: tuple[Construct, ...] = (
    # Literals and character sets.
    Construct("literal", "a"),
    Construct("literal-alt", "b"),
    Construct("dot", "."),
    Construct("class", "[ab]"),
    Construct("class-negated", "[^a]"),
    Construct("class-range", "[a-c]"),
    # Shorthand classes. Keys match `_CLASS_ESCAPES`.
    Construct("escape-d", r"\d"),
    Construct("escape-D", r"\D"),
    Construct("escape-w", r"\w"),
    Construct("escape-W", r"\W"),
    Construct("escape-s", r"\s"),
    Construct("escape-S", r"\S"),
    # Literal escapes. Keys match `_LITERAL_ESCAPES`.
    Construct("escape-a", r"\a"),
    Construct("escape-n", r"\n"),
    Construct("escape-t", r"\t"),
    Construct("escape-r", r"\r"),
    Construct("escape-f", r"\f"),
    Construct("escape-v", r"\v"),
    # Numeric escapes.
    Construct("escape-x", r"\x61"),
    Construct("escape-u", r"b"),
    Construct("escape-U", r"\U00000063"),
    Construct("escape-N", r"\N{BULLET}"),
    Construct("escape-octal", r"\101"),
    Construct("escape-octal-zero", r"\0"),
    # Escapes inside a class, where the decoding is a separate code path.
    Construct("class-escape-hex", r"[\x61-\x63]"),
    Construct("class-escape-literal", r"[\a]"),
    Construct("class-escape-backspace", r"[\b]"),
    Construct("class-escape-shorthand", r"[\d]"),
    # Repetition.
    Construct("star", "a*"),
    Construct("plus", "a+"),
    Construct("optional", "a?"),
    Construct("repeat-exact", "a{2}"),
    Construct("repeat-range", "a{1,2}"),
    Construct("repeat-open", "a{2,}"),
    Construct("repeat-no-lower", "a{,2}"),
    Construct("lazy", "a*?"),
    # Grouping and alternation.
    Construct("group", "(ab)"),
    Construct("group-noncapturing", "(?:ab)"),
    Construct("group-named", "(?P<x>ab)"),
    Construct("alternation", "a|b"),
    # Zero-width.
    Construct("boundary", r"\b"),
    Construct("boundary-negated", r"\B"),
    Construct("anchor-start", "^"),
    Construct("anchor-end", "$"),
    # Lookaround. Keys match the openers `_group` accepts.
    Construct("lookahead", "(?=a)"),
    Construct("lookahead-negative", "(?!a)"),
    Construct("lookbehind", "(?<=a)"),
    Construct("lookbehind-negative", "(?<!a)"),
    Construct("lookaround-nested-at-start", "(?=(?=a))"),
    Construct("lookaround-nullable-body", "(?!a?)"),
    # Sticky quantifiers: transparent only where the body matches one way.
    Construct("possessive", "(a*+)", requires="a*+"),
    Construct("atomic", "(?>a)", requires="(?>a)"),
    # dk.brics operators.
    Construct("brics-intersect", "(a)&(a)", dialect=Dialect.BRICS),
    Construct("brics-complement", "~(a)", dialect=Dialect.BRICS),
    Construct("brics-any", "@", dialect=Dialect.BRICS),
    Construct("brics-empty", "#", dialect=Dialect.BRICS),
)


def available(construct: Construct) -> bool:
    """Whether the running interpreter's `re` accepts this construct."""
    if construct.requires is None:
        return True
    try:
        re.compile(construct.requires)
    except re.error:
        return False
    return True


def atoms_for(dialect: Dialect = Dialect.PYTHON) -> list[str]:
    """The atoms a generator should draw from for `dialect`."""
    return [c.atom for c in SYNTAX if c.dialect is dialect and available(c)]
