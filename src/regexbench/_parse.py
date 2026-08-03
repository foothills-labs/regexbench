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

import unicodedata
from dataclasses import dataclass

from .types import Dialect, Semantics

__all__ = ["parse", "Unsupported", "NonRegular", "Node"]

# Sentinels standing for "a character not named anywhere in the patterns",
# one per class of unnamed character that a pattern can tell apart. `\\d` and
# `[0-9]` are different languages in Python — `\\d` matches every Unicode digit,
# `٣` included — so an alphabet that lumped all unnamed characters together
# would report them equivalent. There is a sentinel per shorthand class for
# that reason, and `\\b` needs the word/non-word split on top.
UNNAMED_DIGIT = "\x00UNNAMED_D"
UNNAMED_WORD = "\x00UNNAMED_W"
UNNAMED_SPACE = "\x00UNNAMED_S"
UNNAMED_OTHER = "\x00UNNAMED_O"
SENTINELS = (UNNAMED_DIGIT, UNNAMED_WORD, UNNAMED_SPACE, UNNAMED_OTHER)

_DIGITS = frozenset("0123456789")
_WORD = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
_SPACE = frozenset(" \t\n\r\f\v")


def is_word_symbol(symbol: str) -> bool:
    """Whether an alphabet symbol counts as a word character for `\\b`.

    Python defines `\\w` on text as "alphanumeric plus underscore", by the
    Unicode definition of alphanumeric — so an accented letter is a word
    character and `\\bé` matches "é".
    """
    if symbol in SENTINELS:
        return symbol in (UNNAMED_DIGIT, UNNAMED_WORD)
    return symbol.isalnum() or symbol == "_"


def in_class(symbol: str, name: str) -> bool:
    """Whether a concrete character belongs to shorthand class `name`."""
    if name == "d":
        return unicodedata.category(symbol) == "Nd"
    if name == "w":
        return symbol.isalnum() or symbol == "_"
    return symbol.isspace()


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
    # Shorthand classes this set draws from — a subset of {"d", "w", "s"}. The
    # enumerated `chars` cover their ASCII members so that witnesses stay
    # readable; the class name covers the rest of Unicode, which cannot be
    # enumerated and must not be silently dropped.
    classes: frozenset[str] = frozenset()

    def accepts(self, symbol: str) -> bool:
        if symbol in SENTINELS:
            return self._covers_class(symbol) != self.negated
        contains = symbol in self.chars or any(
            in_class(symbol, name) for name in self.classes
        )
        return contains != self.negated

    def _covers_class(self, sentinel: str) -> bool:
        if sentinel == UNNAMED_DIGIT:
            # Digits are word characters, so `\\w` covers them too.
            return bool(self.classes & {"d", "w"})
        if sentinel == UNNAMED_WORD:
            return "w" in self.classes
        if sentinel == UNNAMED_SPACE:
            return "s" in self.classes
        return False  # nothing shorthand covers "none of the above"


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
class Anchor(Node):
    """A start or end anchor: `^` or `$`.

    Under full-match semantics `^` at the very start and `$` at the very end
    are the identity, so the parser folds those away and only records the
    flags for SEARCH semantics. Any other anchor — inside a group, mid-branch,
    mid-concatenation — is resolved by `_resolve_anchors` against what may
    precede or follow it, so an anchor that can never hold collapses the
    language to empty, exactly as Python's engine treats it.
    """

    is_start: bool  # True for `^`, False for `$`


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
        # The search reduction widens the pattern with `.*`, which would
        # move any nested anchor away from the position it constrains.
        # Pattern-edge anchors were already folded into the flags; the
        # rest are refused rather than mis-answered.
        if _contains_anchor(node):
            raise Unsupported("anchors are only supported at the pattern edges")
    else:
        node, _ = _resolve_anchors(node, prefix_nullable=True, suffix_nullable=True)
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
    # Truly any character, not `.` — `.` excludes newlines and `re.search`
    # does not. Searching for "a" finds it in "\na", so a wrapper built from
    # `.` would wrongly forbid the surrounding text from containing newlines.
    any_run = Repeat(any_char(), 0, None)

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


def _nullable(node: Node) -> bool:
    """Whether the empty string belongs to the language."""
    if isinstance(node, Empty) or isinstance(node, Assert) or isinstance(node, Anchor):
        return True
    if isinstance(node, CharSet):
        return False  # a character set never matches the empty string
    if isinstance(node, Concat):
        return all(_nullable(part) for part in node.parts)
    if isinstance(node, Alternate):
        return any(_nullable(option) for option in node.options)
    if isinstance(node, Repeat):
        return node.minimum == 0 or _nullable(node.node)
    if isinstance(node, Intersect):
        return all(_nullable(part) for part in node.parts)
    return not _nullable(node.node)  # Complement


def _contains_anchor(node: Node) -> bool:
    if isinstance(node, Anchor):
        return True
    if isinstance(node, Concat):
        return any(_contains_anchor(part) for part in node.parts)
    if isinstance(node, Alternate):
        return any(_contains_anchor(option) for option in node.options)
    if isinstance(node, Repeat):
        return _contains_anchor(node.node)
    return False


def _resolve_anchors(
    node: Node, *, prefix_nullable: bool, suffix_nullable: bool
) -> tuple[Node, bool]:
    """Fold every anchor away, matching what Python's engine can match.

    An anchor is where the text says it is: `^` is satisfiable only if the
    whole match up to it is the empty string, `$` only if the whole match
    after it is. If that cannot happen the branch matches nothing — Python
    compiles `a^` and it simply never matches. If it can, the anchor is the
    identity and the side it guards collapses to the empty string: `a?^c`
    is `c`, not `a?c`, because a non-empty `a?` would put `^` at position
    one. This is why `a?^` is the empty pattern and `a*^b` is `b`.

    The two flags say whether the text on each side *can* be empty. A nested
    anchor needs more than that: `a?(^a)` is `a`, not `a?a`, because the `^`
    demands that the text before the group actually *is* empty. Inside a
    repetition at most one iteration can carry a non-empty match — a second
    one would sit at position one or later — and the `^`-bearing iteration
    must be the first, the `$`-bearing one the last, so `(^a)*` is `a?`,
    `(^a)+` is `a` and `(^a){2}` matches nothing. Exact counts mix these
    shapes through `first · middle* · last`, and when the body also has
    anchor-free paths those repeat freely around the anchored ones.

    Returns the resolved node and whether any anchor survived it (resolved
    to the identity rather than to the empty language).
    """
    if isinstance(node, Anchor):
        holds = prefix_nullable if node.is_start else suffix_nullable
        if holds:
            return Empty(), True
        return CharSet(frozenset()), False
    if isinstance(node, Alternate):
        options = []
        held = False
        for option in node.options:
            resolved, survived = _resolve_anchors(
                option, prefix_nullable=prefix_nullable, suffix_nullable=suffix_nullable
            )
            options.append(resolved)
            held = held or survived
        return Alternate(tuple(options)), held
    if isinstance(node, Repeat):
        inner, held = _resolve_anchors(
            node.node, prefix_nullable=prefix_nullable, suffix_nullable=suffix_nullable
        )
        if not _contains_anchor(node.node) or not held:
            return Repeat(inner, node.minimum, node.maximum), held
        if node.maximum == 0:
            return Empty(), True
        # An anchored repetition: the `^`-bearing iteration must be first
        # (every earlier one matches the empty string) and the `$`-bearing
        # one last. Non-empty iterations can therefore only appear as the
        # single iteration, or as a first/middle/last triple.
        head, _ = _resolve_anchors(
            node.node, prefix_nullable=prefix_nullable, suffix_nullable=False
        )
        mid, _ = _resolve_anchors(node.node, prefix_nullable=False, suffix_nullable=False)
        tail, _ = _resolve_anchors(
            node.node, prefix_nullable=False, suffix_nullable=suffix_nullable
        )
        options = []
        if node.minimum == 0:
            options.append(Empty())
        if _nullable(inner):
            options.append(Empty())
        if node.minimum <= 1 <= (node.maximum if node.maximum is not None else 10**9):
            options.append(inner)
        if node.maximum is None or node.maximum >= 2:
            e_filler = _nullable(head) or _nullable(mid) or _nullable(tail)
            if e_filler:
                options.append(inner)
            if e_filler:
                lo = 0
            else:
                lo = max(0, node.minimum - 2)
            hi = node.maximum - 2 if node.maximum is not None else None
            options.append(Concat((head, Repeat(mid, lo, hi), tail)))
        return (
            options[0] if len(options) == 1 else Alternate(tuple(options)),
            True,
        )
    if isinstance(node, Concat):
        parts = list(node.parts)
        if not parts:
            return Empty(), False
        # A `^` demands that everything before it be the empty string; a `$`
        # that everything after it be. Every start-anchor's demand is implied
        # by the last start-anchor's, and every end-anchor's by the first
        # end-anchor's, so only those two matter.
        start = -1
        end = len(parts)
        for index, part in enumerate(parts):
            if isinstance(part, Anchor):
                if part.is_start:
                    start = index
                elif end == len(parts):
                    end = index
        front_ok = prefix_nullable and all(_nullable(p) for p in parts[:start])
        back_ok = suffix_nullable and all(_nullable(p) for p in parts[end + 1 :])
        if start >= 0 and not front_ok:
            return CharSet(frozenset()), False
        if end < len(parts) and not back_ok:
            return CharSet(frozenset()), False
        if start > end:
            # The demands overlap: `b$^` needs the `b` to be empty, `$b^c`'s
            # `^` sits after the `b$`... everything must be the empty string,
            # or the concatenation matches nothing.
            if all(_nullable(p) for p in parts):
                return Empty(), True
            return CharSet(frozenset()), False
        if start != -1 or end != len(parts):
            # The anchors at `start` and `end` hold: what lies before the
            # last start-anchor and after the first end-anchor is empty, and
            # the two anchors are themselves the identity. Only the middle
            # remains, still inside the surrounding match.
            middle = Concat(tuple(parts[start + 1 : end]))
            resolved, survived = _resolve_anchors(
                middle, prefix_nullable=prefix_nullable, suffix_nullable=suffix_nullable
            )
            return resolved, True or survived
        # No anchors at this level; every part may carry its own. A nested
        # `^` holds only when the actual text before the part is empty, a
        # nested `$` only when the text after it is — "can be empty" is not
        # enough (`a?(^a)` is `a`, not `a?a`). Each part therefore resolves
        # in up to four contexts — free (both sides occupied), start-anchored
        # (`^`s alive), end-anchored (`$`s alive), both — and the parts
        # combine over every legal assignment: the start-anchored part must
        # be preceded only by empty parts, the end-anchored one followed only
        # by empty ones, and at most one of each can carry a non-empty match.
        n = len(parts)
        gate_f = [True] * n
        gate_b = [True] * n
        gate_f_nodes = [
            _resolve_anchors(p, prefix_nullable=prefix_nullable, suffix_nullable=False)[0]
            for p in parts
        ]
        gate_b_nodes = [
            _resolve_anchors(p, prefix_nullable=False, suffix_nullable=suffix_nullable)[0]
            for p in parts
        ]
        for i in range(1, n):
            gate_f[i] = all(_nullable(gate_f_nodes[j]) for j in range(i))
        for i in range(n - 1):
            gate_b[i] = all(_nullable(gate_b_nodes[j]) for j in range(i + 1, n))
        free = [
            _resolve_anchors(p, prefix_nullable=False, suffix_nullable=False)[0]
            for p in parts
        ]
        start_mode = [
            _resolve_anchors(
                p, prefix_nullable=prefix_nullable and gate_f[i], suffix_nullable=False
            )[0]
            for i, p in enumerate(parts)
        ]
        end_mode = [
            _resolve_anchors(
                p, prefix_nullable=False, suffix_nullable=suffix_nullable and gate_b[i]
            )[0]
            for i, p in enumerate(parts)
        ]
        both_mode = [
            _resolve_anchors(
                p,
                prefix_nullable=prefix_nullable and gate_f[i],
                suffix_nullable=suffix_nullable and gate_b[i],
            )
            for i, p in enumerate(parts)
        ]
        held = any(survived for _, survived in both_mode)
        restrict_f = [_epsilon_restrict(g) for g in gate_f_nodes]
        restrict_b = [_epsilon_restrict(g) for g in gate_b_nodes]
        terms: list[Node] = [_join(free)]
        if all(
            _nullable(
                _resolve_anchors(
                    p, prefix_nullable=prefix_nullable, suffix_nullable=suffix_nullable
                )[0]
            )
            for p in parts
        ):
            terms.append(
                _join(
                    [
                        _epsilon_restrict(
                            _resolve_anchors(
                                p,
                                prefix_nullable=prefix_nullable,
                                suffix_nullable=suffix_nullable,
                            )[0]
                        )
                        for p in parts
                    ]
                )
            )
        for i in range(n):
            if gate_f[i]:
                terms.append(
                    _join(restrict_f[:i] + [start_mode[i]] + free[i + 1 :])
                )
            if gate_b[i]:
                terms.append(
                    _join(free[:i] + [end_mode[i]] + restrict_b[i + 1 :])
                )
            if gate_f[i] and gate_b[i]:
                terms.append(
                    _join(restrict_f[:i] + [both_mode[i][0]] + restrict_b[i + 1 :])
                )
        for i in range(n):
            if not gate_f[i]:
                continue
            for j in range(i + 1, n):
                if gate_b[j]:
                    terms.append(
                        _join(
                            restrict_f[:i]
                            + [start_mode[i]]
                            + free[i + 1 : j]
                            + [end_mode[j]]
                            + restrict_b[j + 1 :]
                        )
                    )
        return terms[0] if len(terms) == 1 else Alternate(tuple(terms)), held
    return node, False


def _epsilon_restrict(node: Node) -> Node:
    """The empty-string part of `node`, with its assertions kept.

    An assertion consumes no characters yet still constrains its position, so
    it survives a part being forced to match the empty string; everything else
    collapses to the empty string (it was already checked to be nullable) or
    to the empty language.
    """
    if isinstance(node, Assert):
        return node
    if isinstance(node, Empty) or isinstance(node, Anchor):
        return Empty()
    if isinstance(node, CharSet):
        return CharSet(frozenset())
    if isinstance(node, Concat):
        return _join([_epsilon_restrict(p) for p in node.parts])
    if isinstance(node, Alternate):
        return Alternate(tuple(_epsilon_restrict(o) for o in node.options))
    if isinstance(node, Repeat):
        if node.minimum == 0:
            return Empty()
        return _epsilon_restrict(node.node)
    if isinstance(node, Intersect):
        # An intersection matches the empty string where every operand does,
        # so each operand's assertions still constrain the position.
        return Intersect(tuple(_epsilon_restrict(p) for p in node.parts))
    return Empty()  # Complement: its ε part is unconstrained by what it negates


def _join(nodes: list[Node]) -> Node:
    """Concatenate resolved parts, keeping the empty/singleton cases plain."""
    if not nodes:
        return Empty()
    return nodes[0] if len(nodes) == 1 else Concat(tuple(nodes))


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
            # Only the true pattern edges are identities under full-match
            # semantics. Anywhere else the anchor still has a meaning — `a^`
            # matches nothing, `(^a)` matches `a` — decided later by
            # `_resolve_anchors`, which knows what may surround the anchor.
            if ch == "^" and self.pos == 1:
                self.anchored_start = True
                return Empty()
            if ch == "$" and self.pos == len(self.src):
                self.anchored_end = True
                return Empty()
            return Anchor(is_start=(ch == "^"))
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
                # Not NonRegular: lookaround alone preserves regularity, so
                # this is decidable and merely unimplemented. Only combining it
                # with backreferences escapes the regular languages.
                raise Unsupported("lookaround is not supported")
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
            return CharSet(chars, negated=negated, classes=frozenset(ch.lower()))
        literal = _LITERAL_ESCAPES.get(ch, ch)
        self.literals.add(literal)
        return CharSet(frozenset(literal))

    def _char_class(self) -> Node:
        negated = False
        if self.peek() == "^":
            self.eat()
            negated = True

        chars: set[str] = set()
        classes: set[str] = set()
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
                    classes.add(esc.lower())
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
        return CharSet(frozenset(chars), negated=negated, classes=frozenset(classes))
