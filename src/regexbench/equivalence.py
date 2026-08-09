"""Deciding whether two patterns describe the same language.

Exact-match scoring is the wrong metric for generated regex: `[0-9]+` and
`\\d+` are the same language and different strings. This compiles both sides to
DFAs and compares them, which is the standard DFA-EQ metric in the regex
generation literature.

Equivalence is defined over **full matches**, matching `re.fullmatch`.
"""

from __future__ import annotations

from ._automata import build_dfa, find_distinguishing_string
from ._parse import (
    UNNAMED_DIGIT,
    UNNAMED_OTHER,
    UNNAMED_SPACE,
    UNNAMED_WORD,
    NonRegular,
    Unsupported,
    parse,
)
from .types import Dialect, EquivalenceResult, Semantics, Verdict

__all__ = ["equivalent", "is_regular"]

# Concrete stand-ins for "some character neither pattern names". Any character
# works as long as neither pattern mentions it — but the two sentinels mean
# different things, so a word character must stand in for one and a non-word
# character for the other, or a witness involving `\\b` would not reproduce.
# One candidate list per sentinel: a character in that class, ideally one a
# reader recognises. Non-ASCII members matter — `\d` and `[0-9]` differ only on
# characters like ٣, so a witness separating them has to be one.
_FILLERS = {
    UNNAMED_DIGIT: "٣٤۵",
    UNNAMED_WORD: "xyzabcXYZ_defghijklmnopqrstuvwé",
    UNNAMED_SPACE: " \t\xa0",
    UNNAMED_OTHER: "!~.-+@#%^&*()[]{}<>/\\|:;\'\"`,?$€\x01",
}


def equivalent(
    left: str,
    right: str,
    *,
    semantics: Semantics = Semantics.FULLMATCH,
    dialect: Dialect = Dialect.PYTHON,
) -> EquivalenceResult:
    """Decide whether `left` and `right` match exactly the same strings.

    Returns UNDECIDABLE — not a guess — when either pattern uses
    backreferences, which put it outside the regular languages. Lookaround
    stays inside them and is decided exactly: lookahead and fixed-width
    lookbehind build into the automata. What it cannot answer comes back
    UNSUPPORTED with a reason.

    Under SEARCH semantics the question becomes "do these two patterns accept
    the same *subject strings* when searched", which is the right question for
    corpora like Re(gEx|DoS)Eval whose references are unanchored. A witness is
    still a real string, now one that one pattern finds and the other does not.
    """
    if left == right:
        # Reflexivity needs no automaton. A pattern's language is a function of
        # its text, so identical text denotes identical languages under any
        # dialect and either semantics — including for backreferences, where
        # nothing else here can reach a verdict. The reference
        # tooling in this field does the same: Re(gEx|DoS)Eval's DFA_Equ
        # evaluation returns true on string equality before invoking
        # regex_dfa_equals.jar at all, so matching that keeps scores comparable.
        return EquivalenceResult(Verdict.EQUIVALENT, reason="identical patterns")

    parsed = []
    for side, pattern in (("left", left), ("right", right)):
        try:
            parsed.append(parse(pattern, semantics=semantics, dialect=dialect))
        except NonRegular as exc:
            return EquivalenceResult(Verdict.UNDECIDABLE, reason=f"{side} pattern: {exc}")
        except Unsupported as exc:
            return EquivalenceResult(Verdict.UNSUPPORTED, reason=f"{side} pattern: {exc}")
    (left_ast, left_chars), (right_ast, right_chars) = parsed

    named = left_chars | right_chars
    # A sentinel only earns a place in the alphabet if some character it could
    # stand for is actually unnamed — otherwise it would yield a witness that
    # does not reproduce.
    alphabet = tuple(sorted(named))
    fillers: dict[str, str] = {}
    for sentinel, candidates in _FILLERS.items():
        filler = next((c for c in candidates if c not in named), None)
        if filler is not None:
            alphabet += (sentinel,)
            fillers[sentinel] = filler

    try:
        left_dfa = build_dfa(left_ast, alphabet)
        right_dfa = build_dfa(right_ast, alphabet)
    except Unsupported as exc:
        return EquivalenceResult(Verdict.UNSUPPORTED, reason=str(exc))

    witness = find_distinguishing_string(left_dfa, right_dfa, fillers)
    if witness is None:
        return EquivalenceResult(Verdict.EQUIVALENT)

    accepted_by = left if left_dfa.accepts(witness) else right
    return EquivalenceResult(
        Verdict.DIFFERENT,
        witness=witness,
        reason=f"{witness!r} is matched by {accepted_by!r} only",
    )


def is_regular(
    pattern: str,
    *,
    semantics: Semantics = Semantics.FULLMATCH,
    dialect: Dialect = Dialect.PYTHON,
) -> bool:
    """Whether `pattern` stays inside the regular languages.

    False means equivalence checking cannot apply — fall back to
    example-based evaluation.

    Pass the `semantics` a task will be scored under, because the answer
    depends on it: an anchor away from the pattern ends is resolved exactly
    under FULLMATCH and refused under SEARCH, where the ``.*p.*`` rewrite
    cannot express it. Asking with the default while scoring a search corpus
    overstates what the engine will actually decide.
    """
    try:
        parse(pattern, semantics=semantics, dialect=dialect)
    except NonRegular:
        return False
    except Unsupported:
        return False
    return True
