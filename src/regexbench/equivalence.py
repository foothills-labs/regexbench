"""Deciding whether two patterns describe the same language.

Exact-match scoring is the wrong metric for generated regex: `[0-9]+` and
`\\d+` are the same language and different strings. This compiles both sides to
DFAs and compares them, which is the standard DFA-EQ metric in the regex
generation literature.

Equivalence is defined over **full matches**, matching `re.fullmatch`.
"""

from __future__ import annotations

from ._automata import build_dfa, find_distinguishing_string
from ._parse import OTHER_NONWORD, OTHER_WORD, NonRegular, Unsupported, parse
from .types import Dialect, EquivalenceResult, Semantics, Verdict

__all__ = ["equivalent", "is_regular"]

# Concrete stand-ins for "some character neither pattern names". Any character
# works as long as neither pattern mentions it — but the two sentinels mean
# different things, so a word character must stand in for one and a non-word
# character for the other, or a witness involving `\\b` would not reproduce.
_WORD_FILLERS = "xyzabcXYZABC0123456789_defghijklmnopqrstuvwDEFGHIJKLMNOPQRSTUVW"
_NONWORD_FILLERS = " !~.-+@#%^&*()[]{}<>/\\|:;\'\"`,?$\x01"


def equivalent(
    left: str,
    right: str,
    *,
    semantics: Semantics = Semantics.FULLMATCH,
    dialect: Dialect = Dialect.PYTHON,
) -> EquivalenceResult:
    """Decide whether `left` and `right` match exactly the same strings.

    Returns UNDECIDABLE — not a guess — when either pattern uses
    backreferences or lookaround, which put it outside the regular languages.

    Under SEARCH semantics the question becomes "do these two patterns accept
    the same *subject strings* when searched", which is the right question for
    corpora like Re(gEx|DoS)Eval whose references are unanchored. A witness is
    still a real string, now one that one pattern finds and the other does not.
    """
    if left == right:
        # Reflexivity needs no automaton. A pattern's language is a function of
        # its text, so identical text denotes identical languages under any
        # dialect and either semantics — including for backreferences and
        # lookaround, where nothing else here can reach a verdict. The reference
        # tooling in this field does the same: Re(gEx|DoS)Eval's DFA_Equ
        # evaluation returns true on string equality before invoking
        # regex_dfa_equals.jar at all, so matching that keeps scores comparable.
        return EquivalenceResult(Verdict.EQUIVALENT, reason="identical patterns")

    try:
        left_ast, left_chars = parse(left, semantics=semantics, dialect=dialect)
    except NonRegular as exc:
        return EquivalenceResult(Verdict.UNDECIDABLE, reason=f"left pattern: {exc}")
    except Unsupported as exc:
        return EquivalenceResult(Verdict.UNSUPPORTED, reason=f"left pattern: {exc}")

    try:
        right_ast, right_chars = parse(right, semantics=semantics, dialect=dialect)
    except NonRegular as exc:
        return EquivalenceResult(Verdict.UNDECIDABLE, reason=f"right pattern: {exc}")
    except Unsupported as exc:
        return EquivalenceResult(Verdict.UNSUPPORTED, reason=f"right pattern: {exc}")

    named = left_chars | right_chars
    # A sentinel only earns a place in the alphabet if some character it could
    # stand for is actually unnamed. `\\w` names every word character there is
    # under this engine's ASCII model, and an OTHER_WORD with no member left to
    # denote would produce witnesses that do not reproduce.
    alphabet = tuple(sorted(named))
    fillers: dict[str, str] = {}
    for sentinel, candidates in (
        (OTHER_WORD, _WORD_FILLERS),
        (OTHER_NONWORD, _NONWORD_FILLERS),
    ):
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


def is_regular(pattern: str, *, dialect: Dialect = Dialect.PYTHON) -> bool:
    """Whether `pattern` stays inside the regular languages.

    False means equivalence checking cannot apply — fall back to
    example-based evaluation.
    """
    try:
        parse(pattern, dialect=dialect)
    except NonRegular:
        return False
    except Unsupported:
        return False
    return True
