"""A parser for the genuinely regular subset of regex syntax.

Equivalence checking works by compiling to finite automata, which is only
possible for patterns that describe regular languages. Anything outside that
subset — backreferences, recursion — is rejected here rather than silently
mishandled downstream. Lookaround stays regular and is parsed into
``Lookaround`` nodes for the automata layer to decide.

Supported: literals, escapes, ``.``, character classes with ranges and
negation, ``*`` ``+`` ``?`` and ``{m,n}`` repetition, alternation, grouping
(capturing or not), and the four lookaround assertions.

Anchors are resolved wherever they appear rather than only at the ends: under
full-match semantics ``^`` holds only if everything before it is empty, so
``a^`` is the empty language and ``a?^c`` is ``c``. Under SEARCH semantics an
anchor away from the ends is still refused, since the ``.*p.*`` rewrite has no
way to express it. Anchors inside a lookaround body are different: there they
describe the position the assertion fires at, so they stay as ``Anchor`` nodes
and the automata layer treats them as absolute string boundaries.

Where Python's own parser refuses a pattern — ``a**``, ``\\b*``, ``\\q``,
``[\\d-z]`` — this refuses it too. A pattern that cannot run under ``re``
should not get a verdict from a tool whose other half runs ``re``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import NoReturn

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

# Whether `\B` matches the empty string on *this* interpreter. CPython 3.14
# changed it to (gh-124130), making `\B` exactly the negation of `\b`; 3.13 and
# earlier refuse it, so `\B` there is the empty language under a full match.
#
# Probed rather than compared against a version number: the running interpreter
# is the authority, since `check()` executes patterns with that same `re`. A
# backport or a rebuilt interpreter would make a version test lie.
NEGATED_BOUNDARY_MATCHES_EMPTY = re.search(r"\B", "") is not None


def _re_accepts(pattern: str) -> bool:
    try:
        re.compile(pattern)
    except re.error:
        return False
    return True


# Possessive quantifiers and atomic groups arrived in CPython 3.11. Probed for
# the same reason the boundary rule is: `check()` and `screen()` run patterns
# with the interpreter's own `re`, so answering a pattern that `re` here cannot
# compile would contradict the other half of every verdict this tool produces.
POSSESSIVE_SUPPORTED = _re_accepts("a*+")
ATOMIC_GROUP_SUPPORTED = _re_accepts("(?>a)")

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
    "a": "\x07", "n": "\n", "t": "\t", "r": "\r", "f": "\f", "v": "\v",
}

# Letters that may follow a backslash outside a class. Everything else is a
# "bad escape" in Python's parser and is refused rather than read literally.
_PATTERN_ESCAPE_LETTERS = frozenset("afnrtvxUuN")

# In a class, `\b` is additionally the backspace character.
_CLASS_LITERAL_ESCAPES = {**_LITERAL_ESCAPES, "b": "\x08"}
_CLASS_ESCAPE_LETTERS = frozenset("abfnrtvxUuN")

_HEX = frozenset("0123456789abcdefABCDEF")


class Unsupported(ValueError):
    """The pattern uses syntax this parser does not implement."""


class NonRegular(Unsupported):
    """The pattern is not a regular language, so automata cannot represent it."""


class Node:
    pass


class UnhandledNode(AssertionError):
    """A walker was handed a node type it does not name.

    Every walker over the AST ends in :func:`_unhandled` rather than a bare
    `return False`, because the recurring way this engine has produced wrong
    answers is a new node type that some walker never learned about. Python's
    own `ast.NodeVisitor` defaults to a silent `generic_visit`; that default is
    exactly the failure, so this goes the other way and fails loudly.

    A node a walker genuinely does not care about is still named — in the
    walker's leaf tuple — so "deliberately nothing to do" reads differently
    from "nobody thought about it". `tests/test_walkers.py` runs every node
    type through every walker and fails on this exception.
    """


def _unhandled(node: Node, walker: str) -> NoReturn:
    raise UnhandledNode(
        f"{walker} does not handle {type(node).__name__}; add it to the walker "
        f"(or to its leaf tuple if there is genuinely nothing to do)"
    )


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

    Regular even though it looks like lookaround — the condition depends only
    on the two characters either side of the position, so a finite automaton
    can carry it in its state.

    The empty string is the one place the two are not exact opposites, and
    only on some interpreters: see :data:`NEGATED_BOUNDARY_MATCHES_EMPTY`.
    """

    negated: bool = False


@dataclass(frozen=True)
class Lookaround(Node):
    """A zero-width lookaround assertion: `(?=...)`, `(?!...)`, `(?<=...)`, `(?<!...)`.

    Regular (its body is a regular language, matched against the remaining or
    the preceding text), so the automata layer decides it rather than the
    parser refusing it. Only when combined with backreferences does a pattern
    escape the regular languages.

    ``kind`` is the opening that introduced it, one of ``"="``, ``"!"``,
    ``"<="``, ``"<!"``. The body is parsed with its anchors kept as
    :class:`Anchor` nodes: inside a lookaround ``^`` means "the current
    position is the start of the string" and ``$`` "the end", which is a
    property of the position the assertion fires at, not of the pattern text.
    Python fixes the width of lookbehind bodies, so ``kind`` ``"<="`` or
    ``"<!"`` requires the body to have a fixed width, decided at parse time.
    """

    kind: str
    body: Node

    @property
    def lookahead(self) -> bool:
        return self.kind in ("=", "!")

    @property
    def positive(self) -> bool:
        return self.kind in ("=", "<=")


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


@dataclass(frozen=True)
class Poss(Node):
    """A possessive quantifier: the repetition count, once chosen, is sealed.

    That only changes the language when the inner atom can match several ways
    (then the sealed greedy choice may differ from the plain reading) or when
    later text could backtrack into the repetition. The parser records the
    construction and `_finalize_sticky` decides, once the whole tree is
    known, whether this one sits where the languages agree.
    """

    node: Node
    minimum: int
    maximum: int | None


@dataclass(frozen=True)
class Atomic(Node):
    """An atomic group `(?>...)`: the group's match is sealed when it exits.

    Python seals the group's *whole* match, greedy choice and all, so
    atomicity is only transparent when the content can match exactly one way
    — no repetition, no alternation. Anything else changes the language and
    is refused rather than answered with the plain-group reading.
    """

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
    node = _finalize_sticky(node, at_end=True)
    if _assert_meets_lookaround(node):
        # The marked machine crosses an assertion only while consuming a real
        # character, because that is when the boundary either side of it is
        # known — but a marker fires before any character is consumed, so a
        # `\b` sitting immediately in front of one can never be crossed and
        # the branch silently matches nothing. Refuse rather than answer.
        raise Unsupported(
            "a word boundary immediately before a lookaround is not supported"
        )
    if semantics is Semantics.SEARCH:
        # The search reduction widens the pattern with `.*`, which would
        # move any nested anchor away from the position it constrains.
        # Pattern-edge anchors are folded into the flags; a `^`/`$` that
        # sits behind only zero-width atoms (`(?=...)`, `\b`, ...) still
        # marks the pattern edge, so it is folded the same way. Anything
        # else is refused rather than mis-answered.
        node, anchored_flags = _fold_edge_anchors(node, parser)
        if _contains_anchor(node):
            raise Unsupported("anchors are only supported at the pattern edges")
        if anchored_flags[1]:
            # A folded `$` still allows one trailing newline — Python's `$`
            # matches just before a newline that ends the subject, and the
            # search reduction's `[\n]?` needs the newline to exist in the
            # alphabet or there is no symbol for it to consume.
            parser.literals.add("\n")
    else:
        if _end_anchor_meets_newline(node, False):
            raise Unsupported(
                "`$` before text that can match a newline is not supported; "
                "Python's `$` also matches just before a string-final newline"
            )
        node, _ = _resolve_anchors(node, prefix_nullable=True, suffix_nullable=True)
    if semantics is Semantics.SEARCH:
        node = _widen_for_search(node, *anchored_flags)
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
    if isinstance(node, (Complement, Poss, Atomic)):
        return uses_assertions(node.node)
    if isinstance(node, Lookaround):
        # An assertion inside a lookaround body belongs to that body's own
        # constraint machine, which carries its own entry context, so it does
        # not make the surrounding operand context-sensitive.
        return False
    if isinstance(node, (Empty, CharSet, Anchor)):
        return False
    _unhandled(node, "uses_assertions")


def any_char() -> CharSet:
    """A set matching every character, including the OTHER sentinel."""
    return CharSet(frozenset(), negated=True)


def _right_run_has_assert(node: Node) -> bool:
    r"""Whether a match of `node` can end on an unresolved `\b`/`\B`."""
    if isinstance(node, Assert):
        return True
    if isinstance(node, Concat):
        for part in reversed(node.parts):
            if _right_run_has_assert(part):
                return True
            if not _nullable(part):
                return False
        return False
    if isinstance(node, Alternate):
        return any(_right_run_has_assert(o) for o in node.options)
    if isinstance(node, (Repeat, Poss)):
        return node.maximum != 0 and _right_run_has_assert(node.node)
    if isinstance(node, Atomic):
        return _right_run_has_assert(node.node)
    if isinstance(node, (Empty, CharSet, Anchor, Lookaround, Intersect, Complement)):
        return False
    _unhandled(node, "_right_run_has_assert")


def _left_run_has_lookaround(node: Node) -> bool:
    """Whether a match of `node` can begin by firing a lookaround marker."""
    if isinstance(node, Lookaround):
        return True
    if isinstance(node, Concat):
        for part in node.parts:
            if _left_run_has_lookaround(part):
                return True
            if not _nullable(part):
                return False
        return False
    if isinstance(node, Alternate):
        return any(_left_run_has_lookaround(o) for o in node.options)
    if isinstance(node, (Repeat, Poss)):
        return node.maximum != 0 and _left_run_has_lookaround(node.node)
    if isinstance(node, Atomic):
        return _left_run_has_lookaround(node.node)
    if isinstance(node, (Empty, CharSet, Assert, Anchor, Intersect, Complement)):
        return False
    _unhandled(node, "_left_run_has_lookaround")


def _assert_meets_lookaround(node: Node) -> bool:
    """Whether a `\b` can sit immediately in front of a lookaround marker.

    A nullable atom between them does not help: when it matches the empty
    string the marker fires at the boundary's own position, so `\b(x?)(?=b)b`
    is just as unrepresentable as `\b(?=b)b`. Only an atom that *cannot* be
    empty pushes the marker past a character, letting the boundary resolve.
    """
    if isinstance(node, Concat):
        parts = node.parts
        for i, part in enumerate(parts):
            if not _right_run_has_assert(part):
                continue
            for later in parts[i + 1 :]:
                if _left_run_has_lookaround(later):
                    return True
                if not _nullable(later):
                    break
        return any(_assert_meets_lookaround(p) for p in parts)
    if isinstance(node, Alternate):
        return any(_assert_meets_lookaround(o) for o in node.options)
    if isinstance(node, Repeat):
        body = node.node
        # A second iteration puts the body's tail in front of its own head.
        if (node.maximum is None or node.maximum > 1) and _right_run_has_assert(
            body
        ) and _left_run_has_lookaround(body):
            return True
        return _assert_meets_lookaround(body)
    if isinstance(node, Lookaround):
        return _assert_meets_lookaround(node.body)
    if isinstance(node, Intersect):
        return any(_assert_meets_lookaround(p) for p in node.parts)
    if isinstance(node, (Complement, Poss, Atomic)):
        return _assert_meets_lookaround(node.node)
    if isinstance(node, (Empty, CharSet, Assert, Anchor)):
        return False
    _unhandled(node, "_assert_meets_lookaround")


def _matches_newline(node: Node) -> bool:
    """Whether `node` can match the one-character string "\\n".

    Over-approximated on purpose: every caller uses this to decide whether to
    refuse, so answering "yes" when the truth is "no" costs coverage and
    answering "no" when the truth is "yes" costs correctness. Concatenation
    ignores whether the other parts are nullable, and a complement is assumed
    to contain the newline, both in the safe direction.
    """
    if isinstance(node, CharSet):
        return node.accepts("\n")
    if isinstance(node, Concat):
        return any(_matches_newline(part) for part in node.parts)
    if isinstance(node, Alternate):
        return any(_matches_newline(option) for option in node.options)
    if isinstance(node, Intersect):
        return any(_matches_newline(part) for part in node.parts)
    if isinstance(node, (Repeat, Poss)):
        return node.maximum != 0 and _matches_newline(node.node)
    if isinstance(node, Atomic):
        return _matches_newline(node.node)
    if isinstance(node, Complement):
        return True
    if isinstance(node, (Empty, Assert, Anchor, Lookaround)):
        return False  # zero-width: matches the empty string, never "\n"
    _unhandled(node, "_matches_newline")


def _end_anchor_meets_newline(node: Node, after_newline: bool) -> bool:
    r"""Whether some `$` in `node` can have exactly a newline after it.

    Python's `$` is not "end of string": without `re.MULTILINE` it matches at
    the end *and* immediately before a newline that ends the string, so
    `re.fullmatch(r"a$\n", "a\n")` matches. Everything downstream here folds
    `$` away as plain end-of-string, which is the same thing whenever the
    text after the anchor cannot be that one trailing newline — and a wrong
    answer when it can. `a$\n` came back as the empty language, so this engine
    called it different from `a\n`.

    Under SEARCH the reduction already refuses any anchor that is not at a
    pattern edge, which is why only full-match verdicts were affected. This
    is the same refusal drawn tighter: a `$` at the end of the pattern is
    still decided, because nothing can follow it.

    `after_newline` says whether the text following this node, inside the
    match, can be exactly "\n". A lookaround body gets `True` unconditionally:
    what follows a `$` inside it is the rest of the subject, which the body
    does not constrain.
    """
    if isinstance(node, Anchor):
        return after_newline and not node.is_start
    if isinstance(node, Concat):
        found = False
        tail = after_newline
        for part in reversed(node.parts):
            found = found or _end_anchor_meets_newline(part, tail)
            tail = tail or _matches_newline(part)
        return found
    if isinstance(node, Alternate):
        return any(_end_anchor_meets_newline(o, after_newline) for o in node.options)
    if isinstance(node, Intersect):
        return any(_end_anchor_meets_newline(p, after_newline) for p in node.parts)
    if isinstance(node, (Repeat, Poss)):
        # A further iteration puts the body's own text after the anchor.
        repeats = node.maximum is None or node.maximum > 1
        inner = after_newline or (repeats and _matches_newline(node.node))
        return _end_anchor_meets_newline(node.node, inner)
    if isinstance(node, (Complement, Atomic)):
        return _end_anchor_meets_newline(node.node, after_newline)
    if isinstance(node, Lookaround):
        return _end_anchor_meets_newline(node.body, True)
    if isinstance(node, (Empty, CharSet, Assert)):
        return False
    _unhandled(node, "_end_anchor_meets_newline")


def _refuse_lookaround_operand(parts: list[Node], operator: str) -> None:
    """Refuse a lookaround inside a `&` or `~` operand.

    The operand is determinized on its own and entered through a context
    gate carrying only the word-ness of the preceding character and whether
    the position is the string start. A lookbehind needs the preceding *text*,
    which that gate cannot express, so `a(((?<=a)b)&(b))` came out as the
    empty language instead of matching "ab".
    """
    if any(_contains_lookaround(part) for part in parts):
        raise Unsupported(f"lookaround inside a {operator!r} operand is not supported")


def _contains_lookaround(node: Node) -> bool:
    if isinstance(node, Lookaround):
        return True
    if isinstance(node, (Concat, Intersect)):
        return any(_contains_lookaround(p) for p in node.parts)
    if isinstance(node, Alternate):
        return any(_contains_lookaround(o) for o in node.options)
    if isinstance(node, (Repeat, Complement, Poss, Atomic)):
        return _contains_lookaround(node.node)
    if isinstance(node, (Empty, CharSet, Assert, Anchor)):
        return False
    _unhandled(node, "_contains_lookaround")


def _nested_fires_at_start(node: Node) -> bool:
    """Whether every lookaround inside `node` fires at `node`'s own position.

    The marked machine records an assertion as a single zero-width edge, and
    chains the markers of assertions nested in its body onto that same edge —
    which is only true when the nested one fires where the outer one does.
    `(?=(?=^a))a` does; `(?=a(?=b))ab` does not, because the inner assertion
    sits one character into the body. Rather than certify the inner
    constraint at the wrong position, the offset case is refused.
    """
    if isinstance(node, Lookaround):
        return True
    if isinstance(node, Concat):
        seen_consuming = False
        for part in node.parts:
            if seen_consuming and _contains_lookaround(part):
                return False
            if not _nested_fires_at_start(part):
                return False
            if not _zero_width(part):
                seen_consuming = True
        return True
    if isinstance(node, Alternate):
        return all(_nested_fires_at_start(option) for option in node.options)
    if isinstance(node, Repeat):
        # A second iteration starts past the first, so only a body that
        # cannot repeat past its own start keeps the firing position.
        if _contains_lookaround(node.node) and not (
            node.maximum == 1 or _zero_width(node.node)
        ):
            return False
        return _nested_fires_at_start(node.node)
    if isinstance(node, (Intersect, Complement)):
        return not _contains_lookaround(node)
    if isinstance(node, (Poss, Atomic)):
        return _nested_fires_at_start(node.node)
    if isinstance(node, (Empty, CharSet, Assert, Anchor)):
        return True
    _unhandled(node, "_nested_fires_at_start")


def _nested_always_fires(node: Node) -> bool:
    """Whether every lookaround in `node` fires on every path through it.

    A nested assertion's marker rides the outer's edge, so the chain certifies
    it whenever the outer fires — unconditionally. That is only the body's own
    condition when the body cannot skip it. `(?=(?![0-9])?a)` can: `re` is free
    to take the branch where the inner assertion never runs, and the chain
    enforced it anyway. An alternation is the same problem from the other side,
    since the chain fires every branch's markers no matter which branch the
    body takes.
    """
    if isinstance(node, Lookaround):
        # Its own body was checked when it was parsed; bodies nest inwards.
        return True
    if isinstance(node, Concat):
        return all(_nested_always_fires(part) for part in node.parts)
    if isinstance(node, Alternate):
        return not any(_contains_lookaround(option) for option in node.options)
    if isinstance(node, Repeat):
        if node.minimum == 0 and _contains_lookaround(node.node):
            return False
        return _nested_always_fires(node.node)
    if isinstance(node, (Poss, Atomic)):
        return _nested_always_fires(node.node)
    if isinstance(node, (Intersect, Complement)):
        return not _contains_lookaround(node)
    if isinstance(node, (Empty, CharSet, Assert, Anchor)):
        return True
    _unhandled(node, "_nested_always_fires")


def _zero_width(node: Node) -> bool:
    """Whether `node` can only match the empty string."""
    if isinstance(node, (Empty, Assert, Anchor)):
        return True
    if isinstance(node, Lookaround):
        return True
    if isinstance(node, Concat):
        return all(_zero_width(part) for part in node.parts)
    if isinstance(node, Alternate):
        return all(_zero_width(option) for option in node.options)
    if isinstance(node, Repeat):
        return node.minimum == 0 and node.maximum == 0
    if isinstance(node, (Poss, Atomic)):
        return _zero_width(node.node)
    if isinstance(node, (CharSet, Intersect, Complement)):
        return False
    _unhandled(node, "_zero_width")


def _fold_edge_anchors(
    node: Node, parser: _Parser
) -> tuple[Node, tuple[bool, bool]]:
    """Fold a `^`/`$` behind only zero-width atoms into the search flags.

    The literal first and last characters of the pattern fold in the
    parser. The same folding applies when an anchor follows only zero-width
    atoms: ``(?!^0*$)...^\\d{1,5}`` under search semantics means the pattern
    must match at the very start of the string, which is exactly what
    ``anchored_start`` already encodes. ``(?!…)``, ``(?=…)``, ``\\b`` and
    groups of them all match the empty string, so a `^` behind them is still
    the pattern edge. Folding only happens when every option reaches the
    anchor with zero width, so a mid-pattern anchor still refuses rather
    than mis-answering.
    """
    anchored_start = parser.anchored_start
    anchored_end = parser.anchored_end
    if isinstance(node, Concat) and node.parts:
        parts = list(node.parts)
        # Skip over zero-width atoms — lookarounds, boundaries, empties.
        # Only an `Anchor` itself gets folded into the search flags; the
        # assertions stay, since they still constrain the same position.
        index = 0
        while index < len(parts):
            part = parts[index]
            if isinstance(part, Anchor):
                if not part.is_start:
                    break
                # Replaced, not removed. Dropping it can leave a bare
                # `Alternate`, and `_widen_for_search` distributes its
                # wildcards over an alternation's branches — right for
                # `^a|b$`, where each anchor binds to one branch, wrong for
                # `(?:^(a|b)$)`, where both bind to the whole group. The
                # parser's own folding leaves an `Empty()` here for the same
                # reason.
                parts[index] = Empty()
                anchored_start = True
                break
            if not _zero_width(part):
                break
            index += 1
        index = len(parts) - 1
        while index >= 0:
            part = parts[index]
            if isinstance(part, Anchor):
                if part.is_start:
                    break
                parts[index] = Empty()
                anchored_end = True
                break
            if not _zero_width(part):
                break
            index -= 1
        # `_join`, not `Concat`: folding both anchors out of `(^)($)` empties
        # the list, and a `Concat(())` reaches `_emit`, which indexes
        # `parts[-1]` and raises IndexError instead of returning a verdict.
        node = _join(parts)
    return node, (anchored_start, anchored_end)


def _widen_for_search(node: Node, anchored_start: bool, anchored_end: bool) -> Node:
    """Wrap `node` so full-matching it is the same as searching the original."""
    # Truly any character, not `.` — `.` excludes newlines and `re.search`
    # does not. Searching for "a" finds it in "\na", so a wrapper built from
    # `.` would wrongly forbid the surrounding text from containing newlines.
    any_run = Repeat(any_char(), 0, None)
    # What a folded `$` still allows after the match. Python's `$` is not
    # end-of-string: without `re.MULTILINE` it also matches immediately before
    # a newline that ends the subject, so `re.search("b$", "b\n")` finds it.
    # Dropping the trailing wildcard outright would forbid that newline and
    # make this engine call `(a|\n)b` different from `(a|\n)b$`.
    trailing_newline = Repeat(CharSet(frozenset("\n")), 0, 1)

    def wrap(inner: Node, prefix: bool, suffix: bool) -> Node:
        parts: list[Node] = []
        if prefix:
            parts.append(any_run)
        parts.append(inner)
        parts.append(any_run if suffix else trailing_newline)
        return Concat(tuple(parts))

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
    if isinstance(node, Lookaround):
        # Zero-width, so it never contributes characters — nullable in the
        # width sense whatever its body does, exactly like `\b`. Whether the
        # assertion *holds* is a property of the position, decided by the
        # automaton; reading the body's nullability here made `(?=a)` look
        # like a consuming atom and collapsed `(?=a)^a` to the empty language.
        return True
    if isinstance(node, (Poss, Atomic)):
        return _nullable(node.node)
    if isinstance(node, Complement):
        return not _nullable(node.node)
    _unhandled(node, "_nullable")


def _contains_anchor(node: Node) -> bool:
    # Anchors inside lookaround bodies are absolute — `^` means "this is the
    # start of the string" wherever the assertion fires — so they are the
    # automaton's business, not the search-rewrite's.
    if isinstance(node, Lookaround):
        return False
    if isinstance(node, Anchor):
        return True
    if isinstance(node, Concat):
        return any(_contains_anchor(part) for part in node.parts)
    if isinstance(node, Alternate):
        return any(_contains_anchor(option) for option in node.options)
    if isinstance(node, (Repeat, Poss, Atomic)):
        return _contains_anchor(node.node)
    if isinstance(node, Intersect):
        return any(_contains_anchor(part) for part in node.parts)
    if isinstance(node, Complement):
        return _contains_anchor(node.node)
    if isinstance(node, (Empty, CharSet, Assert)):
        return False
    _unhandled(node, "_contains_anchor")


def _fixed_width(node: Node) -> int | None:
    """The common length of every string `node` matches, or None.

    Python refuses a lookbehind whose body can match different lengths, so the
    parser makes the same refusal and says what it is instead of compiling a
    machine that would silently approximate.
    """
    if isinstance(node, (Empty, Assert, Anchor, Lookaround)):
        return 0
    if isinstance(node, CharSet):
        return 1
    if isinstance(node, Concat):
        widths = [_fixed_width(part) for part in node.parts]
        if any(w is None for w in widths):
            return None
        return sum(widths)  # type: ignore[return-value]
    if isinstance(node, Alternate):
        widths = [_fixed_width(option) for option in node.options]
        if any(w is None for w in widths):
            return None
        if len(set(widths)) != 1:
            return None
        return widths[0]
    if isinstance(node, Repeat):
        if node.minimum != node.maximum:
            return None
        width = _fixed_width(node.node)
        if width is None:
            return None
        return width * node.minimum
    if isinstance(node, Intersect):
        widths = [_fixed_width(part) for part in node.parts]
        if any(w is None for w in widths):
            return None
        if len(set(widths)) != 1:
            return None
        return widths[0]
    if isinstance(node, (Poss, Atomic)):
        return _fixed_width(node.node)
    if isinstance(node, Complement):
        return None  # its words are not all one length
    _unhandled(node, "_fixed_width")


# Resolving an anchor inside a concatenation enumerates which part carries it,
# and nested concatenations multiply those choices. RegexEval's reference 2284
# is fifteen alternations deep inside `^...$` and expands 98 nodes into 48
# million — so the expansion is capped and refused, the same way the automata
# layer caps states rather than grinding on a pattern it cannot finish.
_MAX_RESOLVED_NODES = 200_000


class _ResolveState:
    """Memo tables for one `_resolve_anchors` call, plus the size budget.

    Both tables are keyed on `id(node)`, and both therefore have to hold the
    node itself in the value. Resolution builds nodes as it goes and drops
    most of them again; CPython hands a freed address straight back to the
    next allocation, so an entry whose key outlives its node is an entry a
    *different* node can collide with. That is a wrong answer, not a slow
    one, and it depends on allocation order, which is why it showed up as a
    pattern that disagreed with `re` only when another pattern had been
    resolved first. Keeping a reference makes the address unreusable for as
    long as the entry can be read.
    """

    __slots__ = ("memo", "sizes")

    def __init__(self) -> None:
        self.memo: dict[tuple[int, bool, bool], tuple[Node, tuple[Node, bool]]] = {}
        self.sizes: dict[int, tuple[Node, int]] = {}


def _node_count(node: Node, sizes: dict[int, tuple[Node, int]]) -> int:
    """Nodes in `node`, memoised on identity so shared subtrees cost once.

    The value keeps `node` alive; see `_ResolveState` for why identity keys
    are only safe that way.
    """
    hit = sizes.get(id(node))
    if hit is not None:
        return hit[1]
    if isinstance(node, (Concat, Intersect)):
        total = 1 + sum(_node_count(p, sizes) for p in node.parts)
    elif isinstance(node, Alternate):
        total = 1 + sum(_node_count(o, sizes) for o in node.options)
    elif isinstance(node, (Repeat, Complement, Atomic, Poss)):
        total = 1 + _node_count(node.node, sizes)
    elif isinstance(node, Lookaround):
        total = 1 + _node_count(node.body, sizes)
    elif isinstance(node, (Empty, CharSet, Assert, Anchor)):
        total = 1
    else:
        _unhandled(node, "_node_count")
    sizes[id(node)] = (node, total)
    return total


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
    return _resolve_cached(node, prefix_nullable, suffix_nullable, _ResolveState())


def _resolve_cached(
    node: Node,
    prefix_nullable: bool,
    suffix_nullable: bool,
    cache: _ResolveState,
) -> tuple[Node, bool]:
    """`_resolve_impl` memoised on the node and the two flags.

    A concatenation resolves each of its parts in up to six contexts, so a
    plain recursion costs six to the nesting depth: RegexEval's reference 2284
    is fifteen alternations deep inside `^...$` and did not finish in minutes.
    There are only four flag combinations and the tree is finite, so caching
    on identity bounds the whole traversal to four visits per node.
    """
    key = (id(node), prefix_nullable, suffix_nullable)
    hit = cache.memo.get(key)
    if hit is not None:
        return hit[1]
    result = _resolve_impl(node, prefix_nullable, suffix_nullable, cache)
    if _node_count(result[0], cache.sizes) > _MAX_RESOLVED_NODES:
        raise Unsupported("resolving anchors expands the pattern past the node budget")
    cache.memo[key] = (node, result)
    return result


def _resolve_impl(
    node: Node,
    prefix_nullable: bool,
    suffix_nullable: bool,
    cache: _ResolveState,
) -> tuple[Node, bool]:
    if isinstance(node, Anchor):
        holds = prefix_nullable if node.is_start else suffix_nullable
        if holds:
            return Empty(), True
        return CharSet(frozenset()), False
    if isinstance(node, Alternate):
        options = []
        held = False
        for option in node.options:
            resolved, survived = _resolve_cached(option, prefix_nullable, suffix_nullable, cache)
            options.append(resolved)
            held = held or survived
        return Alternate(tuple(options)), held
    if isinstance(node, Repeat):
        inner, held = _resolve_cached(node.node, prefix_nullable, suffix_nullable, cache)
        if not _contains_anchor(node.node) or not held:
            return Repeat(inner, node.minimum, node.maximum), held
        if node.maximum == 0:
            return Empty(), True
        # An anchored repetition: the `^`-bearing iteration must be first
        # (every earlier one matches the empty string) and the `$`-bearing
        # one last. Non-empty iterations can therefore only appear as the
        # single iteration, or as a first/middle/last triple.
        head, _ = _resolve_cached(node.node, prefix_nullable, False, cache)
        mid, _ = _resolve_cached(node.node, False, False, cache)
        tail, _ = _resolve_cached(node.node, False, suffix_nullable, cache)
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
                return _join([_epsilon_restrict(p) for p in parts]), True
            return CharSet(frozenset()), False
        if start != -1 or end != len(parts):
            # The anchors at `start` and `end` hold: what lies before the
            # last start-anchor and after the first end-anchor is empty, and
            # the two anchors are themselves the identity. Only the middle
            # remains, still inside the surrounding match.
            #
            # "Empty" is not "gone": a `\b` in the collapsed region consumes
            # nothing but still constrains the position, and `re` fails
            # `($)\b` on the empty subject for exactly that reason. The
            # epsilon-restriction keeps those assertions and drops the rest.
            head_parts = parts[:start] if start >= 0 else []
            tail_parts = parts[end + 1 :]
            middle = Concat(tuple(parts[start + 1 : end]))
            resolved, _ = _resolve_cached(middle, prefix_nullable, suffix_nullable, cache)

            def restrict(part: Node, before: bool, after: bool) -> Node:
                node, _ = _resolve_cached(part, before, after, cache)
                return _epsilon_restrict(node)

            if not any(_contains_anchor(p) for p in (*head_parts, *tail_parts)):
                front = [_epsilon_restrict(p) for p in head_parts]
                back = [_epsilon_restrict(p) for p in tail_parts]
                return _join([*front, resolved, *back]), True

            # A region collapsed to the empty string can still contain an
            # anchor of its own, and dropping it is how `a$(^)+` came back
            # matching "a": `re` needs that `^` at position zero, and the `a`
            # in front of it makes that impossible. The anchor is nested, so
            # the scan above never saw it.
            #
            # Whether it holds depends on the middle, which is the only text
            # that can be non-empty here, so the two cases are taken apart.
            # When the middle matches something, a `^` after it and a `$`
            # before it are both dead; when the middle is empty too, the whole
            # concatenation is, and they hold exactly as the outer flags allow.
            occupied = _join(
                [
                    *[restrict(p, prefix_nullable, False) for p in head_parts],
                    resolved,
                    *[restrict(p, False, suffix_nullable) for p in tail_parts],
                ]
            )
            vacant = _join(
                [
                    *[restrict(p, prefix_nullable, suffix_nullable) for p in head_parts],
                    _epsilon_restrict(resolved),
                    *[
                        restrict(p, prefix_nullable, suffix_nullable)
                        for p in tail_parts
                    ],
                ]
            )
            return Alternate((occupied, vacant)), True
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
            _resolve_cached(p, prefix_nullable, False, cache)[0]
            for p in parts
        ]
        gate_b_nodes = [
            _resolve_cached(p, False, suffix_nullable, cache)[0]
            for p in parts
        ]
        for i in range(1, n):
            gate_f[i] = all(_nullable(gate_f_nodes[j]) for j in range(i))
        for i in range(n - 1):
            gate_b[i] = all(_nullable(gate_b_nodes[j]) for j in range(i + 1, n))
        free = [
            _resolve_cached(p, False, False, cache)[0]
            for p in parts
        ]
        start_mode = [
            _resolve_cached(p, prefix_nullable and gate_f[i], False, cache)[0]
            for i, p in enumerate(parts)
        ]
        end_mode = [
            _resolve_cached(p, False, suffix_nullable and gate_b[i], cache)[0]
            for i, p in enumerate(parts)
        ]
        both_mode = [
            _resolve_cached(p, prefix_nullable and gate_f[i], suffix_nullable and gate_b[i], cache)
            for i, p in enumerate(parts)
        ]
        held = any(survived for _, survived in both_mode)
        restrict_f = [_epsilon_restrict(g) for g in gate_f_nodes]
        restrict_b = [_epsilon_restrict(g) for g in gate_b_nodes]
        terms: list[Node] = [_join(free)]
        if all(
            _nullable(
                _resolve_cached(p, prefix_nullable, suffix_nullable, cache)[0]
            )
            for p in parts
        ):
            terms.append(
                _join(
                    [
                        _epsilon_restrict(
                            _resolve_cached(p, prefix_nullable, suffix_nullable, cache)[0]
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


def _operands(node: Node) -> tuple[Node, ...]:
    """The child nodes of a branching node, whichever field holds them."""
    if isinstance(node, Alternate):
        return node.options
    return node.parts  # Concat, Intersect


def _epsilon_restrict(node: Node) -> Node:
    """The empty-string part of `node`, with its assertions kept.

    An assertion consumes no characters yet still constrains its position, so
    it survives a part being forced to match the empty string; everything else
    collapses to the empty string (it was already checked to be nullable) or
    to the empty language.
    """
    if isinstance(node, Assert) or isinstance(node, Lookaround):
        # Both consume nothing and both still constrain the position, so a
        # region forced to the empty string keeps them. Dropping the
        # lookaround made `(?!a?)a` equivalent to `a`, when it matches nothing.
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
    if isinstance(node, (Poss, Atomic)):
        return _epsilon_restrict(node.node)
    if isinstance(node, Complement):
        # Its ε part is unconstrained by what it negates.
        return Empty()
    _unhandled(node, "_epsilon_restrict")


def _join(nodes: list[Node]) -> Node:
    """Concatenate resolved parts, keeping the empty/singleton cases plain."""
    if not nodes:
        return Empty()
    return nodes[0] if len(nodes) == 1 else Concat(tuple(nodes))


def _ambiguous(node: Node) -> bool:
    """Whether `node` can match a given position several different ways.

    Repetitions pick a count and alternations pick a branch, so both are
    ambiguous; everything else is determined by the input. A lookaround is
    zero-width and either holds or not, so it never adds ambiguity.
    """
    if isinstance(node, (Repeat, Poss, Alternate)):
        return True
    if isinstance(node, (Concat, Intersect)):
        return any(_ambiguous(part) for part in node.parts)
    if isinstance(node, (Atomic, Complement)):
        return _ambiguous(node.node)
    if isinstance(node, Lookaround):
        # The assertion is zero-width and either holds or not: the body's own
        # ambiguity only concerns the body's match, which decides the
        # assertion deterministically. So the group never adds ambiguity.
        return False
    if isinstance(node, (Poss, Atomic)):
        return _ambiguous(node.node)
    if isinstance(node, (Intersect, Complement)):
        return True  # operand shape is not modelled; assume it can vary
    if isinstance(node, (Empty, CharSet, Assert, Anchor)):
        return False
    _unhandled(node, "_ambiguous")

def _sticky_problem(node: Node) -> bool:
    """Whether `node` contains a possessive repeat or an atomic group whose
    sealed match could diverge from the plain-language reading.

    A possessive repetition is always a problem: it seals a count that the
    surrounding repetitions are unaware of. An atomic group is only a problem
    when its content is ambiguous, so `(?>(ab))*` is fine while `(?>a|ab)*`
    is not.
    """
    if isinstance(node, Poss):
        return True
    if isinstance(node, Atomic):
        return _ambiguous(node.node)
    if isinstance(node, (Concat, Intersect)):
        return any(_sticky_problem(part) for part in node.parts)
    if isinstance(node, (Repeat, Complement)):
        return _sticky_problem(node.node)
    if isinstance(node, Lookaround):
        return _sticky_problem(node.body)
    if isinstance(node, (Alternate, Intersect)):
        return any(_sticky_problem(p) for p in _operands(node))
    if isinstance(node, (Empty, CharSet, Assert, Anchor)):
        return False
    _unhandled(node, "_sticky_problem")


def _finalize_sticky(node: Node, at_end: bool) -> Node:
    """Resolve possessive quantifiers and atomic groups to plain repeats.

    A possessive quantifier is provably the same language as its plain
    spelling exactly when nothing follows it that could backtrack into it
    (`at_end`) *and* the repeated atom can only match one way — a sealed
    greedy choice over an ambiguous atom differs even alone, as `(a|ab){2}+`
    does from `(a|ab){2}`. An atomic group is transparent exactly when its
    content can only match one way, wherever it sits: `(?>a|ab)` fullmatches
    only "a", so even alone it is not `a|ab`. Any other placement is refused
    with `Unsupported` rather than answered with the wrong language.
    """
    if isinstance(node, Poss):
        if not at_end or _ambiguous(node.node):
            raise Unsupported(
                "possessive quantifiers need an unambiguous atom at the end of a branch"
            )
        return Repeat(node.node, node.minimum, node.maximum)
    if isinstance(node, Atomic):
        if _ambiguous(node.node):
            raise Unsupported(
                "atomic groups with repetition or alternation inside are not supported"
            )
        return _finalize_sticky(node.node, at_end)
    if isinstance(node, Repeat):
        if _sticky_problem(node.node):
            raise Unsupported(
                "possessive quantifiers and ambiguous atomic groups cannot be repeated"
            )
        return Repeat(_finalize_sticky(node.node, False), node.minimum, node.maximum)
    if isinstance(node, Concat):
        n = len(node.parts)
        return Concat(
            tuple(
                _finalize_sticky(part, at_end and index == n - 1)
                for index, part in enumerate(node.parts)
            )
        )
    if isinstance(node, Alternate):
        return Alternate(tuple(_finalize_sticky(option, at_end) for option in node.options))
    if isinstance(node, Intersect):
        return Intersect(
            tuple(_finalize_sticky(part, at_end) for part in node.parts)
        )
    if isinstance(node, Complement):
        return Complement(_finalize_sticky(node.node, at_end))
    if isinstance(node, Lookaround):
        # The body is matched against a window with nothing following it
        # inside the assertion, so a trailing possessive quantifier in the
        # body is as transparent as at the end of a branch.
        return Lookaround(node.kind, _finalize_sticky(node.body, at_end=True))
    if isinstance(node, (Empty, CharSet, Assert, Anchor)):
        return node
    _unhandled(node, "_finalize_sticky")


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
        # Set by parse_atom for zero-width atoms (assertions, anchors, and
        # pattern-edge anchors folded to Empty) and consumed by parse_repeat,
        # which must refuse to quantify them — unless a group wraps them.
        self._atom_is_zero_width = False
        # Set by parse_atom for group atoms: Python lets a quantifier follow a
        # group-wrapped repeat (`(a+)+` is fine) but not a bare one (`a++` is
        # "multiple repeat"), so the parser needs to know where the atom came
        # from. Consumed by the first quantifier in parse_repeat.
        self._atom_from_group = False

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
        if len(parts) == 1:
            return parts[0]
        _refuse_lookaround_operand(parts, "&")
        return Intersect(tuple(parts))

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
        if self._atom_is_zero_width:
            ch = self.peek()
            # A `{` only counts as a quantifier when it forms valid bounds;
            # `\b{2, 3}` is literal text in Python, while `\b{2}` and `\b?`
            # are "nothing to repeat".
            if ch in ("*", "+", "?") or (ch == "{" and self._try_bounds() is not None):
                raise Unsupported(f"nothing to repeat at position {self.pos - 1}")
        self._atom_is_zero_width = False
        while True:
            ch = self.peek()
            if ch not in ("*", "+", "?", "{"):
                break
            if ch == "{":
                bounds = self._try_bounds()
                if bounds is None:
                    break  # `{` is not a quantifier here; leave it as literal text
                minimum, maximum = bounds
            else:
                self.eat()
                minimum, maximum = {"*": (0, None), "+": (1, None), "?": (0, 1)}[ch]
            if isinstance(node, (Repeat, Poss)) and not self._atom_from_group:
                raise Unsupported(f"multiple repeat at position {self.pos - 1}")
            self._atom_from_group = False
            node = Repeat(node, minimum, maximum)

            # Lazy (`?`) and possessive (`+`) modifiers change the matching
            # strategy, not the repetition itself. Laziness never changes the
            # language; possessiveness only does when later text could
            # backtrack into the repetition, which `_finalize_sticky` decides.
            if self.peek() == "?":
                self.eat()
            elif self.peek() == "+":
                self.eat()
                if not POSSESSIVE_SUPPORTED:
                    raise Unsupported(
                        "possessive quantifiers need CPython 3.11 or newer"
                    )
                node = Poss(node.node, node.minimum, node.maximum)
        return node

    def _try_bounds(self) -> tuple[int, int | None] | None:
        start = self.pos
        self.eat()  # {
        digits = ""
        while self.peek() is not None and self.peek().isdigit():
            digits += self.eat()
        if not digits and self.peek() != ",":
            # `a{}` really is literal text; `a{,3}` is not — Python reads an
            # omitted lower bound as zero, so `{,3}` is `{0,3}`.
            self.pos = start
            return None
        low = int(digits) if digits else 0
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
            inner = self.parse_complement()
            _refuse_lookaround_operand([inner], "~")
            return Complement(inner)
        return self.parse_atom()

    def parse_atom(self) -> Node:
        ch = self.eat()

        if ch == "(":
            node = self._group()
            self._atom_from_group = True
            return node
        if ch == "[":
            return self._char_class()
        if ch == ".":
            if self.brics:
                # dk.brics' dot matches every character, the newline included:
                # its patterns are over plain strings, with no line concept.
                return any_char()
            # `.` matches every character except the newline — the one
            # Unicode distinction it draws without naming a character. The
            # newline has to join the alphabet or it is conflated with the
            # rest of the whitespace sentinel, and `a.` comes back equivalent
            # to `ab|a[^b]` although the two differ on "a\n".
            self.literals.add("\n")
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
                self._atom_is_zero_width = True
                return Empty()
            if ch == "$" and self.pos == len(self.src):
                self.anchored_end = True
                self._atom_is_zero_width = True
                return Empty()
            self._atom_is_zero_width = True
            return Anchor(is_start=(ch == "^"))
        if ch in "*+?":
            raise Unsupported(f"nothing to repeat at position {self.pos - 1}")

        self.literals.add(ch)
        return CharSet(frozenset(ch))

    def _group(self) -> Node:
        atomic = False
        if self.src.startswith("?", self.pos):
            rest = self.src[self.pos:]
            if rest.startswith("?:"):
                self.pos += 2
            elif rest.startswith(("?=", "?!", "?<=", "?<!")):
                # Lookaround keeps the language regular, so this parses into a
                # `Lookaround` node for the automata layer to decide. Anchors
                # inside the body are absolute — `^` means "the current
                # position is the start of the string", `$` the end — so they
                # stay as `Anchor` nodes rather than being resolved here.
                opened = ""
                for opener, kind in (
                    ("?<=", "<="),
                    ("?<!", "<!"),
                    ("?=", "="),
                    ("?!", "!"),
                ):
                    if rest.startswith(opener):
                        self.pos += len(opener)
                        opened = kind
                        break
                body = self.parse_alternation()
                if self.peek() != ")":
                    raise Unsupported("unbalanced parenthesis")
                self.eat()
                if opened in ("<=", "<!") and _fixed_width(body) is None:
                    raise Unsupported("look-behind requires fixed-width pattern")
                if not _nested_fires_at_start(body):
                    raise Unsupported(
                        "a lookaround nested past the start of another's body "
                        "is not supported"
                    )
                if opened != "=" and _contains_lookaround(body):
                    # A nested assertion is checked by chaining its marker
                    # onto this one's, which certifies it against the real
                    # subject at *this* position. That is exactly the body's
                    # own condition only under a positive lookahead, whose
                    # body begins where the assertion fires.
                    #
                    # Under a negative one it is De Morgan run backwards:
                    # `(?!(?=a)b)` fails when the conjunction fails, and the
                    # chain asks instead that neither conjunct hold, so
                    # `(?!(?!a))a` matched nothing where `re` matches "a".
                    # Under a lookbehind the position is wrong as well — the
                    # body ends at the firing position and starts a body's
                    # width earlier — so `(?<=(?<=a)b)` was certified against
                    # the text after the match and rejected "ab".
                    raise Unsupported(
                        "a lookaround nested inside a negative lookaround or "
                        "a lookbehind is not supported"
                    )
                if not _nested_always_fires(body):
                    raise Unsupported(
                        "a lookaround nested in a body that can skip it is "
                        "not supported"
                    )
                return Lookaround(kind=opened, body=body)
            elif rest.startswith("?>"):
                # Atomicity changes the language only when the content can
                # match several ways and later text could backtrack into it;
                # the position check happens in `_finalize_sticky`.
                self.pos += 2
                atomic = True
            elif rest.startswith(("?P=", "?P>")):
                # `(?P=name)` is a named backreference and `(?P>name)` a
                # subroutine call; both are outside the regular languages.
                raise NonRegular("backreferences make the language non-regular")
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
        if atomic and not ATOMIC_GROUP_SUPPORTED:
            raise Unsupported("atomic groups need CPython 3.11 or newer")
        return Atomic(node) if atomic else node

    def _escape(self) -> Node:
        if self.pos >= len(self.src):
            raise Unsupported("pattern ends with a backslash")
        ch = self.eat()

        if ch in {"b", "B"}:
            # dk.brics defines \b as the literal 'b'; the corpora that use this
            # dialect mean a word boundary, and their paired descriptions say
            # so ("lines using words ending in 'er'"). The intent wins, and the
            # deviation is documented rather than silent.
            # Deliberately does not add the word characters to the alphabet:
            # every symbol already has a well-defined word-ness, the sentinels
            # included, so naming all 63 would multiply the alphabet — and the
            # DFA — for nothing.
            self._atom_is_zero_width = True
            return Assert(negated=(ch == "B"))
        if ch in {"A", "Z", "z", "G"}:
            raise Unsupported(f"anchor escape \\{ch} is not supported")
        if ch in _CLASS_ESCAPES:
            chars, negated = _CLASS_ESCAPES[ch]
            self.literals.update(chars)
            return CharSet(chars, negated=negated, classes=frozenset(ch.lower()))
        if ch.isdigit() and ch != "0":
            # `\123` is an octal escape exactly when all three digits are
            # octal (Python reads the first three); anything else of this
            # shape is a group reference.
            if (
                ch in "1234567"
                and self.pos + 1 < len(self.src)
                and self.src[self.pos] in "01234567"
                and self.src[self.pos + 1] in "01234567"
            ):
                literal = self._read_octal_escape(ch)
            else:
                raise NonRegular("backreferences make the language non-regular")
        else:
            if ch.isalpha() and ch not in _PATTERN_ESCAPE_LETTERS:
                raise Unsupported(f"bad escape \\{ch} at position {self.pos - 1}")
            literal = self._decode_escape(ch)
        self.literals.add(literal)
        return CharSet(frozenset(literal))

    def _decode_escape(self, esc: str) -> str:
        """The literal character the escape `esc` denotes.

        `\\xHH`, `\\uHHHH` and `\\UHHHHHHHH` are exactly that many hex digits;
        `\\0` takes up to two further octal digits and anything past that
        stays literal, so `\\0123` is `\\n` + "3" exactly as in Python.
        """
        if esc in {"x", "u", "U"}:
            return self._read_hex_escape({"x": 2, "u": 4, "U": 8}[esc])
        if esc == "0":
            return self._read_octal_escape("0")
        if esc == "N":
            return self._read_named_escape()
        return _LITERAL_ESCAPES.get(esc, esc)

    def _class_escape_literal(self, esc: str) -> str:
        """The literal character a class escape denotes.

        Classes have no group references, so every digit escape is octal
        there, and `\\b` is the backspace character rather than a boundary.
        """
        if esc in "01234567":
            return self._read_octal_escape(esc)
        if esc == "b":
            return _CLASS_LITERAL_ESCAPES["b"]
        return self._decode_escape(esc)

    def _read_hex_escape(self, digits: int) -> str:
        raw = self.src[self.pos : self.pos + digits]
        if len(raw) < digits or any(c not in _HEX for c in raw):
            raise Unsupported(f"incomplete escape \\{self.src[self.pos - 1]}{raw}")
        self.pos += digits
        value = int(raw, 16)
        if value > 0x10FFFF:
            raise Unsupported(f"escape \\{self.src[self.pos - 1]}{raw} is out of range")
        return chr(value)

    def _read_octal_escape(self, first: str) -> str:
        digits = first
        while len(digits) < 3 and self.pos < len(self.src) and self.src[self.pos] in "01234567":
            digits += self.src[self.pos]
            self.pos += 1
        value = int(digits, 8)
        if value > 0o377:
            raise Unsupported(f"octal escape value \\{digits} is out of range")
        return chr(value)

    def _read_named_escape(self) -> str:
        if not self.src.startswith("{", self.pos):
            raise Unsupported("missing { after \\N")
        close = self.src.find("}", self.pos)
        if close == -1:
            raise Unsupported("unterminated \\N{ name")
        name = self.src[self.pos + 1 : close]
        self.pos = close + 1
        try:
            return unicodedata.lookup(name)
        except KeyError:
            raise Unsupported(f"undefined character name {name!r}") from None

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
                        # `[\D]` is exactly `[^\d]`, which is representable —
                        # but only alone: `[\D0-9]` is not one set, and mixing
                        # it with a range or further members cannot be
                        # expressed, so that is refused rather than dropped.
                        if chars or classes or self.peek() != "]":
                            raise Unsupported(
                                f"negated class escape \\{esc} must be the only member of [...]"
                            )
                        self.eat()  # the closing bracket
                        self.literals.update(sub)
                        return CharSet(
                            sub,
                            negated=not negated,
                            classes=frozenset(esc.lower()),
                        )
                    if self.peek() == "-" and self.src[self.pos + 1 : self.pos + 2] not in (
                        "",
                        "]",
                    ):
                        # `[\d-z]` is "bad character range" in Python: a
                        # shorthand class has no code point to range from.
                        raise Unsupported(
                            f"bad character range \\{esc}-{self.src[self.pos + 1]}"
                        )
                    chars.update(sub)
                    classes.add(esc.lower())
                    continue
                if esc in "89" or (
                    esc.isalpha() and esc not in _CLASS_ESCAPE_LETTERS
                ):
                    raise Unsupported(
                        f"bad escape \\{esc} in character class at position {self.pos - 1}"
                    )
                ch = self._class_escape_literal(esc)

            is_range = (
                self.peek() == "-"
                and self.pos + 1 < len(self.src)
                and self.src[self.pos + 1] != "]"
            )
            if is_range:
                self.eat()
                end = self.eat()
                if end == "\\":
                    esc = self.eat()
                    if esc in _CLASS_ESCAPES:
                        raise Unsupported(f"bad character range {ch}-\\{esc}")
                    if esc in "89" or (esc.isalpha() and esc not in _CLASS_ESCAPE_LETTERS):
                        raise Unsupported(
                            f"bad escape \\{esc} in character class at position {self.pos - 1}"
                        )
                    end = self._class_escape_literal(esc)
                if ord(end) < ord(ch):
                    raise Unsupported(f"reversed range {ch}-{end}")
                if ord(end) - ord(ch) > 0x10000:
                    raise Unsupported("character range is too large to enumerate")
                chars.update(chr(c) for c in range(ord(ch), ord(end) + 1))
            else:
                chars.add(ch)

        self.literals.update(chars)
        return CharSet(frozenset(chars), negated=negated, classes=frozenset(classes))
