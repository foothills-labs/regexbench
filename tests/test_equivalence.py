import re

import pytest

from regexbench import Dialect, Semantics, Verdict, equivalent, is_regular


@pytest.mark.parametrize(
    "left,right",
    [
        (r"[0-9]+", r"[0-9][0-9]*"),
        (r"abc", r"abc"),
        (r"a|b", r"[ab]"),
        (r"(ab)+", r"ab(ab)*"),
        (r"a?", r"|a"),
        (r"[a-c]", r"a|b|c"),
        (r"a{2}", r"aa"),
        (r"a{1,3}", r"a|aa|aaa"),
        (r"(?:xy)*", r"(xy)*"),
        (r"\d{2,}", r"\d\d\d*"),
        (r"[^a]", r"[^a]"),
        (r"a*a*", r"a*"),
        (r"(a|b)*", r"[ab]*"),
    ],
)
def test_equivalent_pairs(left, right):
    result = equivalent(left, right)
    assert result.verdict is Verdict.EQUIVALENT, f"{left} vs {right}: {result.reason}"
    assert bool(result) is True


@pytest.mark.parametrize(
    "left,right",
    [
        (r"a+", r"a*"),
        (r"[0-9]", r"[0-9a]"),
        (r"abc", r"abd"),
        (r"a{2}", r"a{3}"),
        (r"\d+", r"\w+"),
        (r"a", r"ab"),
        (r"[^a]", r"."),
    ],
)
def test_different_pairs_report_a_real_witness(left, right):
    result = equivalent(left, right)
    assert result.verdict is Verdict.DIFFERENT, f"{left} vs {right}"
    assert result.witness is not None

    # The witness must genuinely separate them under Python's own engine,
    # otherwise the automata and re disagree and the result is worthless.
    in_left = re.fullmatch(left, result.witness) is not None
    in_right = re.fullmatch(right, result.witness) is not None
    assert in_left != in_right, (
        f"witness {result.witness!r} does not distinguish {left} from {right}"
    )


@pytest.mark.parametrize("pattern", [r"(a)\1", r"a\1"])
def test_backreferences_are_undecidable_not_guessed(pattern):
    """Backreferences leave the regular languages, so equivalence is undecidable."""
    result = equivalent(pattern, r"a")
    assert result.verdict is Verdict.UNDECIDABLE
    assert not is_regular(pattern)


@pytest.mark.parametrize("pattern", [r"(?=a)b", r"(?!a)b", r"(?<=a)b", r"(?<!a)b"])
def test_lookaround_is_unsupported_not_undecidable(pattern):
    """Lookaround alone preserves regularity — this is decidable, just unbuilt.

    Only combining lookaround with backreferences escapes the regular
    languages. Calling it undecidable would be a claim about the problem when
    it is a statement about this engine.
    """
    result = equivalent(pattern, r"a")
    assert result.verdict is Verdict.UNSUPPORTED
    assert not is_regular(pattern), "not analyzable here, whatever the theory says"


def test_undecidable_is_reported_for_either_side():
    assert equivalent(r"a", r"(a)\1").verdict is Verdict.UNDECIDABLE


def test_plain_patterns_are_regular():
    assert is_regular(r"\d{3}-\d{4}")
    assert is_regular(r"[a-z]+@[a-z]+\.[a-z]{2,}")


def test_anchors_at_the_ends_are_ignored_under_fullmatch():
    assert equivalent(r"^abc$", r"abc").verdict is Verdict.EQUIVALENT


def test_dot_excludes_newline():
    result = equivalent(r".", r"[^\n]")
    assert result.verdict is Verdict.EQUIVALENT


def test_unsupported_syntax_is_flagged_separately_from_undecidable():
    result = equivalent(r"a{2,1}", r"a")
    assert result.verdict is Verdict.UNSUPPORTED


def test_a_pattern_is_equivalent_to_itself_even_when_undecidable():
    """Reflexivity needs no automaton.

    A pattern's language is a function of its text, so identical text denotes
    identical languages — including for backreferences and lookaround, where
    nothing else here can reach a verdict. The reference tooling agrees:
    Re(gEx|DoS)Eval's DFA_Equ_Evaluation returns true on string equality before
    invoking regex_dfa_equals.jar, so matching it keeps scores comparable.
    """
    for pattern in (r"(a)\1", r"(?=a)ab", r"\bword\b", r"\d+"):
        result = equivalent(pattern, pattern)
        assert result.verdict is Verdict.EQUIVALENT, f"{pattern!r} differs from itself"
        assert result.witness is None


def test_reflexivity_holds_under_every_setting():
    for kwargs in (
        {},
        {"semantics": Semantics.SEARCH},
        {"dialect": Dialect.BRICS},
        {"semantics": Semantics.SEARCH, "dialect": Dialect.BRICS},
    ):
        assert equivalent(r"~(a)\1", r"~(a)\1", **kwargs).verdict is Verdict.EQUIVALENT


def test_an_intractable_intersection_is_refused_rather_than_attempted():
    """Equivalence with both & and ~ is non-elementary, so the guard matters.

    Sixteen intersected patterns is well inside what a corpus can contain and
    well past what determinizing can afford. It must come back UNSUPPORTED
    quickly rather than exhaust memory.
    """
    pattern = "&".join(f"(.*{c}.*)" for c in "abcdefghijklmnop")
    result = equivalent(pattern, "x", dialect=Dialect.BRICS)
    assert result.verdict is Verdict.UNSUPPORTED
    assert "too many" in result.reason


@pytest.mark.parametrize(
    "left,right,witness",
    [
        (r"\d", "[0-9]", "٣"),
        (r"\w", "[A-Za-z0-9_]", None),
        (r"\s", "[ \t\n\r\f\v]", None),
    ],
)
def test_shorthand_classes_are_unicode_aware(left, right, witness):
    """`\\d` is not `[0-9]`, and `re` is the reason.

    Python matches every Unicode digit with `\\d`, so the two are different
    languages — and `check()` runs the real `re`, so an engine that called them
    equivalent would disagree with the tool it lives in.
    """
    import re

    result = equivalent(left, right)
    assert result.verdict is Verdict.DIFFERENT
    assert result.witness is not None
    assert (re.fullmatch(left, result.witness) is not None) != (
        re.fullmatch(right, result.witness) is not None
    ), f"witness {result.witness!r} does not reproduce the difference"
    if witness is not None:
        assert result.witness == witness
