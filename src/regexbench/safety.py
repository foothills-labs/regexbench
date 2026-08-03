"""ReDoS screening for generated patterns.

Two passes, because each catches what the other misses:

1. **Structural.** Look for the shapes that cause catastrophic backtracking —
   a quantifier wrapping a quantified group, or alternation branches that can
   match the same text. Cheap, and explains *why* a pattern is dangerous.
2. **Empirical.** Actually run the pattern against attack strings under a
   timeout. Catches shapes the structural pass doesn't model.

Neither is a proof of safety. A SAFE verdict means "no known-bad shape and no
blow-up on the strings we tried", which is a screening result, not a
guarantee.
"""

from __future__ import annotations

import re

from ._parse import Alternate, CharSet, Concat, Node, Repeat, Unsupported, parse
from .execute import match_many
from .types import Risk, SafetyResult

__all__ = ["screen", "attack_strings"]

_PROBE_TIMEOUT = 0.5
_PROBE_LENGTHS = (14, 20, 26)


def screen(pattern: str, empirical: bool = True) -> SafetyResult:
    """Screen `pattern` for ReDoS exposure."""
    try:
        re.compile(pattern)
    except re.error as exc:
        return SafetyResult(Risk.SAFE, reason=f"pattern does not compile: {exc}")

    structural = _structural(pattern)
    if structural.risk is Risk.EXPONENTIAL:
        return structural

    if empirical:
        found = _empirical(pattern)
        if found is not None:
            return found

    return structural


def _structural(pattern: str) -> SafetyResult:
    try:
        ast, _ = parse(pattern)
    except Unsupported:
        # Outside the parseable subset — the empirical pass is all we have.
        return SafetyResult(
            Risk.SAFE, reason="not statically analyzable; screened empirically only"
        )

    if _nested_quantifier(ast):
        return SafetyResult(
            Risk.EXPONENTIAL,
            reason="a quantifier wraps a quantified group, e.g. (a+)+ — "
            "exponential backtracking on a failing suffix",
        )
    if _ambiguous_alternation(ast):
        return SafetyResult(
            Risk.EXPONENTIAL,
            reason="a quantifier wraps alternation whose branches overlap, e.g. (a|a)* — "
            "the engine retries every split",
        )
    if _adjacent_quantifiers(ast):
        return SafetyResult(
            Risk.POLYNOMIAL,
            reason="two quantifiers over overlapping character sets in sequence, "
            "e.g. \\s*\\s* — quadratic on a failing match",
        )
    return SafetyResult(Risk.SAFE, reason="no known-vulnerable structure")


def _nested_quantifier(node: Node) -> bool:
    """A repeat whose body can itself repeat, and can match more than one way."""
    if isinstance(node, Repeat):
        if _unbounded(node) and _contains_unbounded_repeat(node.node):
            return True
    return any(_nested_quantifier(child) for child in _children(node))


def _ambiguous_alternation(node: Node) -> bool:
    if isinstance(node, Repeat) and _unbounded(node):
        inner = node.node
        if isinstance(inner, Alternate) and _branches_overlap(inner):
            return True
    return any(_ambiguous_alternation(child) for child in _children(node))


def _adjacent_quantifiers(node: Node) -> bool:
    if isinstance(node, Concat):
        parts = node.parts
        for left, right in zip(parts, parts[1:], strict=False):
            if (
                isinstance(left, Repeat)
                and isinstance(right, Repeat)
                and _unbounded(left)
                and _unbounded(right)
                and _sets_overlap(left.node, right.node)
            ):
                return True
    return any(_adjacent_quantifiers(child) for child in _children(node))


def _branches_overlap(node: Alternate) -> bool:
    sets = [o for o in node.options if isinstance(o, CharSet)]
    for i, left in enumerate(sets):
        for right in sets[i + 1 :]:
            if _sets_overlap(left, right):
                return True
    return False


def _sets_overlap(left: Node, right: Node) -> bool:
    if not isinstance(left, CharSet) or not isinstance(right, CharSet):
        return False
    if left.negated and right.negated:
        return True
    if left.negated or right.negated:
        positive, negative = (right, left) if left.negated else (left, right)
        return bool(positive.chars - negative.chars)
    return bool(left.chars & right.chars)


def _unbounded(node: Repeat) -> bool:
    return node.maximum is None


def _contains_unbounded_repeat(node: Node) -> bool:
    if isinstance(node, Repeat) and _unbounded(node):
        return True
    return any(_contains_unbounded_repeat(child) for child in _children(node))


def _children(node: Node) -> tuple[Node, ...]:
    if isinstance(node, Concat):
        return node.parts
    if isinstance(node, Alternate):
        return node.options
    if isinstance(node, Repeat):
        return (node.node,)
    return ()


def attack_strings(pattern: str, length: int = 20) -> list[str]:
    """Build candidate blow-up inputs for `pattern`.

    The shape that hurts is a long run of something the pattern happily
    consumes, followed by one character that forces the whole match to fail.
    """
    literals = sorted(set(re.findall(r"[A-Za-z0-9]", pattern)))
    seeds = literals[:3] or ["a"]
    if re.search(r"\\d|\[0-9", pattern):
        seeds.append("1")
    if re.search(r"\\s", pattern):
        seeds.append(" ")

    out = []
    for seed in dict.fromkeys(seeds):
        run = seed * length
        out.append(run + "!")
        out.append(run + "\x00")
    return out


def _empirical(pattern: str) -> SafetyResult | None:
    for length in _PROBE_LENGTHS:
        probes = attack_strings(pattern, length)
        if not probes:
            continue
        # One child for all of this length's probes rather than one each: a
        # pattern generates around six per length, and screening was spending
        # nineteen process starts to answer a question that needs three.
        # resume=False because the first hang already settles it.
        try:
            outcomes = match_many(
                pattern, probes, method="search", timeout=_PROBE_TIMEOUT, resume=False
            )
        except re.error:
            return None

        for probe, outcome in zip(probes, outcomes, strict=True):
            if outcome is None:
                risk = Risk.EXPONENTIAL if length <= _PROBE_LENGTHS[0] else Risk.POLYNOMIAL
                return SafetyResult(
                    risk,
                    reason=f"matching did not finish in {_PROBE_TIMEOUT}s on a "
                    f"{len(probe)}-character input",
                    witness=probe,
                )
    return None
