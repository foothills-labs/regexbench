import re

import pytest

from regexbench import Verdict, equivalent, is_regular


@pytest.mark.parametrize(
    "left,right",
    [
        (r"[0-9]+", r"\d+"),
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


@pytest.mark.parametrize(
    "pattern",
    [r"(a)\1", r"(?=a)b", r"(?!a)b", r"(?<=a)b", r"(?<!a)b"],
)
def test_non_regular_features_are_undecidable_not_guessed(pattern):
    result = equivalent(pattern, r"a")
    assert result.verdict is Verdict.UNDECIDABLE
    assert not is_regular(pattern)


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
