"""A parser for the genuinely regular subset of regex syntax.

Equivalence checking works by compiling to finite automata, which is only
possible for patterns that describe regular languages. Anything outside that
subset — backreferences, lookaround, recursion — is rejected here rather than
silently mishandled downstream.

Supported: literals, escapes, ``.``, character classes with ranges and
negation, ``*`` ``+`` ``?`` and ``{m,n}`` repetition, alternation, and
grouping (capturing or not). Anchors are accepted only at the ends, since
equivalence is defined over full matches.
"""

from __future__ import annotations

from dataclasses import dataclass

from .types import Dialect, Semantics

__all__ = ["parse", "Unsupported", "NonRegular", "Node"]

# Sentinels standing for "a character not named anywhere in the patterns".
#
# There are two of them rather than one because `\b` can tell them apart. A
# word boundary asks whether the characters either side of a position are word
# characters, so an alphabet that lumps every unnamed character together cannot
# answer it: `\bx` matches "!x" and not "ax", and both '!' and 'a' would be the
# same symbol. Splitting the sentinel by word-ness keeps the alphabet finite
# and still sound, because within each half the members remain indistinguishable
# to both patterns.
OTHER_WORD = "\x00OTHER_W"
OTHER_NONWORD = "\x00OTHER_N"
SENTINELS = (OTHER_WORD, OTHER_NONWORD)

_DIGITS = frozenset("0123456789")
_WORD = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
_SPACE = frozenset(" \t\n\r\f\v")


def is_word_symbol(symbol: str) -> bool:
    """Whether an alphabet symbol counts as a word character for `\\b`."""
    if symbol == OTHER_WORD:
        return True
    if symbol == OTHER_NONWORD:
        return False
    return symbol in _WORD

_CLASS_ESCAPES: dict[str, tuple[frozenset[str], bool]] = {
    "d": (_DIGITS, False),
    "D": (_DIGITS, True),
    "w": (_WORD, False),
    "W": (_WORD, True),
    "s": (_SPACE, False),
    "S": (_SPACE, True),
}

_LITERAL_ESCAPES = {
    "n": "\n", "t": "\t", "r": "\r", "f": "\f", "v": "\v", "0": "\0",
}


class Unsupported(ValueError):
    """The pattern uses syntax this parser does not implement."""


class NonRegular(Unsupported):
    """The pattern is not a regular language, so automata cannot represent it."""


class Node:
    pass


@dataclass(frozen=True)
class Empty(Node):
    """Matches the empty string."""


@dataclass(frozen=True)
class CharSet(Node):
    """A set of characters, possibly negated.

    Negation is kept symbolic rather than expanded, so the set stays finite
    regardless of how large the real alphabet is.
    """

    chars: frozenset[str]
    negated: bool = False

    def accepts(self, symbol: str) -> bool:
        if symbol in SENTINELS:
            # A sentinel stands for unnamed characters, so a positive set never
            # contains it and a negated set always does.
            return self.negated
        return (symbol in self.chars) != self.negated


@dataclass(frozen=True)
class Concat(Node):
    parts: tuple[Node, ...]


@dataclass(frozen=True)
class Alternate(Node):
    options: tuple[Node, ...]


@dataclass(frozen=True)
class Repeat(Node):
    node: Node
    minimum: int
    maximum: int | None  # None means unbounded


@dataclass(frozen=True)
class Assert(Node):
    """A zero-width word boundary: `\\b`, or `\\B` when negated.

    Regular despite looking like lookaround — the condition depends only on the
    two characters either side of the position, so a finite automaton can carry
    it in its state.
    """

    negated: bool = False


@dataclass(frozen=True)
class Intersect(Node):
    """Strings matched by every branch. dk.brics `&`; no Python equivalent."""

    parts: tuple[Node, ...]


@dataclass(frozen=True)
class Complement(Node):
    """Every string the inner pattern does not match. dk.brics `~`."""

    node: Node


def parse(
    pattern: str,
    *,
    semantics: Semantics = Semantics.FULLMATCH,
    dialect: Dialect = Dialect.PYTHON,
) -> tuple[Node, frozenset[str]]:
    """Parse `pattern`, returning its AST and every literal character in it.

    Under SEARCH semantics the pattern is rewritten to the full-match pattern
    describing the same set of subject strings: ``p`` becomes ``.*p.*``, minus
    the wildcard on whichever end the pattern already anchors. Everything
    downstream then works in full-match terms only, so there is exactly one
    notion of equivalence in the automata layer.

    The dialect must be stated because the two languages overlap without
    agreeing: ``&`` and ``~`` are operators in dk.brics and ordinary literals
    in Python, and ``^``/``$`` are anchors in Python and literals in dk.brics.
    Guessing wrong changes the language silently, which is the one failure
    mode a benchmark cannot afford.
    """
    parser = _Parser(pattern, dialect)
    node = parser.parse_alternation()
    if parser.pos != len(parser.src):
        raise Unsupported(f"unexpected {parser.peek()!r} at position {parser.pos}")
    if semantics is Semantics.SEARCH:
        node = _widen_for_search(node, parser.anchored_start, parser.anchored_end)
    return node, frozenset(parser.literals)


def uses_assertions(node: Node) -> bool:
    """Whether `node` contains a word boundary anywhere, operators included."""
    if isinstance(node, Assert):
        return True
    if isinstance(node, Concat):
        return any(uses_assertions(part) for part in node.parts)
    if isinstance(node, Alternate):
        return any(uses_assertions(option) for option in node.options)
    if isinstance(node, Intersect):
        return any(uses_assertions(part) for part in node.parts)
    if isinstance(node, Repeat):
        return uses_assertions(node.node)
    if isinstance(node, Complement):
        return uses_assertions(node.node)
    return False


def any_char() -> CharSet:
    """A set matching every character, including the OTHER sentinel."""
    return CharSet(frozenset(), negated=True)


def _widen_for_search(node: Node, anchored_start: bool, anchored_end: bool) -> Node:
    """Wrap `node` so full-matching it is the same as searching the original."""
    any_run = Repeat(CharSet(frozenset("\n"), negated=True), 0, None)

    def wrap(inner: Node, prefix: bool, suffix: bool) -> Node:
        parts: list[Node] = []
        if prefix:
            parts.append(any_run)
        parts.append(inner)
        if suffix:
            parts.append(any_run)
        return parts[0] if len(parts) == 1 else Concat(tuple(parts))

    if isinstance(node, Alternate):
        # A leading ^ anchors only the first branch and a trailing $ only the
        # last: in `a|b$` the anchor says nothing about `a`. So the wildcards
        # distribute over the branches instead of wrapping the alternation.
        last = len(node.options) - 1
        return Alternate(
            tuple(
                wrap(
                    option,
                    not (anchored_start and index == 0),
                    not (anchored_end and index == last),
                )
                for index, option in enumerate(node.options)
            )
        )
    return wrap(node, not anchored_start, not anchored_end)


class _Parser:
    def __init__(self, src: str, dialect: Dialect = Dialect.PYTHON) -> None:
        self.src = src
        self.dialect = dialect
        self.brics = dialect is Dialect.BRICS
        self.pos = 0
        self.literals: set[str] = set()
        # Recorded rather than inferred later: the parser folds end anchors
        # into Empty(), so by the time there is an AST the anchor is gone.
        self.anchored_start = False
        self.anchored_end = False

    def peek(self) -> str | None:
        return self.src[self.pos] if self.pos < len(self.src) else None

    def eat(self) -> str:
        ch = self.src[self.pos]
        self.pos += 1
        return ch

    def parse_alternation(self) -> Node:
        options = [self.parse_intersection()]
        while self.peek() == "|":
            self.eat()
            options.append(self.parse_intersection())
        return options[0] if len(options) == 1 else Alternate(tuple(options))

    def parse_intersection(self) -> Node:
        """dk.brics `&`, which binds tighter than `|` and looser than concat."""
        parts = [self.parse_concat()]
        while self.brics and self.peek() == "&":
            self.eat()
            parts.append(self.parse_concat())
        return parts[0] if len(parts) == 1 else Intersect(tuple(parts))

    def parse_concat(self) -> Node:
        parts: list[Node] = []
        stop = "|)&" if self.brics else "|)"
        while True:
            ch = self.peek()
            if ch is None or ch in stop:
                break
            parts.append(self.parse_repeat())
        if not parts:
            return Empty()
        return parts[0] if len(parts) == 1 else Concat(tuple(parts))

    def parse_repeat(self) -> Node:
        node = self.parse_complement()
        while True:
            ch = self.peek()
            if ch == "*":
                self.eat()
                node = Repeat(node, 0, None)
            elif ch == "+":
                self.eat()
                node = Repeat(node, 1, None)
            elif ch == "?":
                self.eat()
                node = Repeat(node, 0, 1)
            elif ch == "{":
                bounds = self._try_bounds()
                if bounds is None:
                    break
                node = Repeat(node, bounds[0], bounds[1])
            else:
                break

            # Possessive quantifiers and lazy modifiers change matching
            # strategy, not the language — but only for lazy. Possessive
            # genuinely changes it, so refuse both rather than guess.
            if self.peek() in {"+", "?"} and isinstance(node, Repeat):
                nxt = self.peek()
                if nxt == "+":
                    raise NonRegular("possessive quantifiers are not supported")
                self.eat()  # lazy: same language, different match choice
        return node

    def _try_bounds(self) -> tuple[int, int | None] | None:
        start = self.pos
        self.eat()  # {
        digits = ""
        while self.peek() is not None and self.peek().isdigit():
            digits += self.eat()
        if not digits:
            self.pos = start
            return None
        low = int(digits)
        if self.peek() == "}":
            self.eat()
            return low, low
        if self.peek() != ",":
            self.pos = start
            return None
        self.eat()
        upper = ""
        while self.peek() is not None and self.peek().isdigit():
            upper += self.eat()
        if self.peek() != "}":
            self.pos = start
            return None
        self.eat()
        high = int(upper) if upper else None
        if high is not None and high < low:
            raise Unsupported(f"invalid repetition bounds {{{low},{high}}}")
        return low, high

    def parse_complement(self) -> Node:
        """dk.brics `~`, which binds tighter than concatenation."""
        if self.brics and self.peek() == "~":
            self.eat()
            return Complement(self.parse_complement())
        return self.parse_atom()

    def parse_atom(self) -> Node:
        ch = self.eat()

        if ch == "(":
            return self._group()
        if ch == "[":
            return self._char_class()
        if ch == ".":
            return CharSet(frozenset("\n"), negated=True)
        if ch == "\\":
            return self._escape()
        if self.brics and ch == "@":
            return Repeat(any_char(), 0, None)  # brics: any string at all
        if self.brics and ch == "#":
            return CharSet(frozenset())  # brics: the empty language
        if ch in "^$" and self.brics:
            # dk.brics has no anchors; these are ordinary characters there.
            self.literals.add(ch)
            return CharSet(frozenset(ch))
        if ch in "^$":
            # Redundant at the ends under full-match semantics; meaningful
            # anywhere else, which this parser cannot express.
            if self.pos == 1 or self.pos == len(self.src):
                if ch == "^" and self.pos == 1:
                    self.anchored_start = True
                elif ch == "$" and self.pos == len(self.src):
                    self.anchored_end = True
                return Empty()
            raise Unsupported(f"anchor {ch!r} is only supported at the pattern ends")
        if ch in "*+?":
            raise Unsupported(f"nothing to repeat at position {self.pos - 1}")

        self.literals.add(ch)
        return CharSet(frozenset(ch))

    def _group(self) -> Node:
        if self.src.startswith("?", self.pos):
            rest = self.src[self.pos:]
            if rest.startswith("?:"):
                self.pos += 2
            elif rest.startswith(("?=", "?!", "?<=", "?<!")):
                raise NonRegular("lookaround makes equivalence undecidable")
            elif rest.startswith("?>"):
                raise NonRegular("atomic groups are not supported")
            elif rest.startswith("?P<") or rest.startswith("?<"):
                close = self.src.find(">", self.pos)
                if close == -1:
                    raise Unsupported("unterminated named group")
                self.pos = close + 1
            else:
                raise Unsupported(f"unsupported group syntax at position {self.pos}")
        node = self.parse_alternation()
        if self.peek() != ")":
            raise Unsupported("unbalanced parenthesis")
        self.eat()
        return node

    def _escape(self) -> Node:
        if self.pos >= len(self.src):
            raise Unsupported("pattern ends with a backslash")
        ch = self.eat()

        if ch.isdigit() and ch != "0":
            raise NonRegular("backreferences make the language non-regular")
        if ch in {"b", "B"}:
            # dk.brics defines \b as the literal 'b'; the corpora that use this
            # dialect mean a word boundary, and their paired descriptions say
            # so ("lines using words ending in 'er'"). The intent wins, and the
            # deviation is documented rather than silent.
            # Deliberately does not add the word characters to the alphabet:
            # every symbol already has a well-defined word-ness, the sentinels
            # included, so naming all 63 would multiply the alphabet — and the
            # DFA — for nothing.
            return Assert(negated=(ch == "B"))
        if ch in {"A", "Z", "z", "G"}:
            raise Unsupported(f"anchor escape \\{ch} is not supported")
        if ch in _CLASS_ESCAPES:
            chars, negated = _CLASS_ESCAPES[ch]
            self.literals.update(chars)
            return CharSet(chars, negated=negated)
        literal = _LITERAL_ESCAPES.get(ch, ch)
        self.literals.add(literal)
        return CharSet(frozenset(literal))

    def _char_class(self) -> Node:
        negated = False
        if self.peek() == "^":
            self.eat()
            negated = True

        chars: set[str] = set()
        first = True
        while True:
            ch = self.peek()
            if ch is None:
                raise Unsupported("unterminated character class")
            if ch == "]" and not first:
                self.eat()
                break
            first = False
            ch = self.eat()

            if ch == "\\":
                if self.pos >= len(self.src):
                    raise Unsupported("character class ends with a backslash")
                esc = self.eat()
                if esc in _CLASS_ESCAPES:
                    sub, sub_negated = _CLASS_ESCAPES[esc]
                    if sub_negated:
                        raise Unsupported(
                            f"negated class escape \\{esc} inside [...] is not supported"
                        )
                    chars.update(sub)
                    continue
                ch = _LITERAL_ESCAPES.get(esc, esc)

            is_range = (
                self.peek() == "-"
                and self.pos + 1 < len(self.src)
                and self.src[self.pos + 1] != "]"
            )
            if is_range:
                self.eat()
                end = self.eat()
                if end == "\\":
                    end = _LITERAL_ESCAPES.get(self.eat(), self.src[self.pos - 1])
                if ord(end) < ord(ch):
                    raise Unsupported(f"reversed range {ch}-{end}")
                if ord(end) - ord(ch) > 0x10000:
                    raise Unsupported("character range is too large to enumerate")
                chars.update(chr(c) for c in range(ord(ch), ord(end) + 1))
            else:
                chars.add(ch)

        self.literals.update(chars)
        return CharSet(frozenset(chars), negated=negated)
