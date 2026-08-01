"""Deciding whether two patterns describe the same language.

Exact-match scoring is the wrong metric for generated regex: `[0-9]+` and
`\\d+` are the same language and different strings. This compiles both sides to
DFAs and compares them, which is the standard DFA-EQ metric in the regex
generation literature.

Equivalence is defined over **full matches**, matching `re.fullmatch`.
"""

from __future__ import annotations

from ._automata import build_dfa, find_distinguishing_string
from ._parse import OTHER, NonRegular, Unsupported, parse
from .types import EquivalenceResult, Verdict

__all__ = ["equivalent", "is_regular"]

# Candidates for the concrete stand-in for "some character neither pattern
# names". Any character works as long as neither pattern mentions it.
_FILLERS = "\x01abcxyz0123456789 !~"


def equivalent(left: str, right: str) -> EquivalenceResult:
    """Decide whether `left` and `right` match exactly the same strings.

    Returns UNDECIDABLE — not a guess — when either pattern uses
    backreferences or lookaround, which put it outside the regular languages.
    """
    try:
        left_ast, left_chars = parse(left)
    except NonRegular as exc:
        return EquivalenceResult(Verdict.UNDECIDABLE, reason=f"left pattern: {exc}")
    except Unsupported as exc:
        return EquivalenceResult(Verdict.UNSUPPORTED, reason=f"left pattern: {exc}")

    try:
        right_ast, right_chars = parse(right)
    except NonRegular as exc:
        return EquivalenceResult(Verdict.UNDECIDABLE, reason=f"right pattern: {exc}")
    except Unsupported as exc:
        return EquivalenceResult(Verdict.UNSUPPORTED, reason=f"right pattern: {exc}")

    named = left_chars | right_chars
    alphabet = (*sorted(named), OTHER)
    filler = next((c for c in _FILLERS if c not in named), None)
    if filler is None:  # pragma: no cover - would need every filler named
        filler = "￿"

    try:
        left_dfa = build_dfa(left_ast, alphabet)
        right_dfa = build_dfa(right_ast, alphabet)
    except Unsupported as exc:
        return EquivalenceResult(Verdict.UNSUPPORTED, reason=str(exc))

    witness = find_distinguishing_string(left_dfa, right_dfa, filler)
    if witness is None:
        return EquivalenceResult(Verdict.EQUIVALENT)

    accepted_by = left if left_dfa.accepts(witness) else right
    return EquivalenceResult(
        Verdict.DIFFERENT,
        witness=witness,
        reason=f"{witness!r} is matched by {accepted_by!r} only",
    )


def is_regular(pattern: str) -> bool:
    """Whether `pattern` stays inside the regular languages.

    False means equivalence checking cannot apply — fall back to
    example-based evaluation.
    """
    try:
        parse(pattern)
    except NonRegular:
        return False
    except Unsupported:
        return False
    return True
