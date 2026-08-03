import pytest

from regexbench import MatchTimeout, Risk, safe_fullmatch, safe_search, screen


@pytest.mark.parametrize("pattern", [r"(a+)+", r"(a*)*", r"([a-z]+)+", r"(a|a)*"])
def test_classic_redos_shapes_are_caught(pattern):
    result = screen(pattern, empirical=False)
    assert result.risk is Risk.EXPONENTIAL, result.reason
    assert result.reason
    assert not result


@pytest.mark.parametrize(
    "pattern",
    [r"\d{3}-\d{4}", r"[a-z]+", r"abc", r"^hello$", r"a?b?c?", r"[0-9]{2,4}"],
)
def test_ordinary_patterns_are_not_flagged(pattern):
    result = screen(pattern, empirical=False)
    assert result.risk is Risk.SAFE, result.reason
    assert bool(result)


@pytest.mark.parametrize("pattern", [r"(a+){2}", r"(a|a){2}", r"(a|a){3}", r"(a|a){2,3}"])
def test_a_bounded_repeat_does_not_explode(pattern):
    # {m} or {m,n} with a finite n fixes the number of repetitions, so
    # backtracking is bounded by the text length raised to that constant
    # power at worst — polynomial, not exponential. Not SAFE either: at
    # `(a+){10}` a few dozen characters already take seconds.
    result = screen(pattern, empirical=False)
    assert result.risk is Risk.POLYNOMIAL, result.reason


@pytest.mark.parametrize("pattern", [r"(a+){1}", r"(ab){10}", r"a+", r"[a-z]+"])
def test_bounded_repeats_without_ambiguity_are_safe(pattern):
    # The bound alone is not the risk — the body has to be ambiguous. `(ab){10}`
    # has exactly one way to consume each iteration, so there is nothing to
    # backtrack over.
    result = screen(pattern, empirical=False)
    assert result.risk is Risk.SAFE, result.reason


@pytest.mark.parametrize("pattern", [r"(a+)+", r"(a|a)*"])
def test_unbounded_repeats_are_still_flagged(pattern):
    result = screen(pattern, empirical=False)
    assert result.risk is Risk.EXPONENTIAL, result.reason


def test_adjacent_overlapping_quantifiers_are_polynomial():
    result = screen(r"\s*\s*x", empirical=False)
    assert result.risk is Risk.POLYNOMIAL


def test_non_overlapping_adjacent_quantifiers_are_fine():
    assert screen(r"\d*[a-z]*", empirical=False).risk is Risk.SAFE


def test_empirical_pass_catches_real_blowup():
    # Structural analysis alone already flags this; the empirical pass must
    # not disagree with it.
    result = screen(r"(a+)+$")
    assert result.risk.is_vulnerable


def test_uncompilable_pattern_is_not_reported_as_dangerous():
    result = screen(r"(unclosed")
    assert result.risk is Risk.SAFE
    assert "does not compile" in result.reason


def test_safe_match_returns_normally():
    assert safe_fullmatch(r"\d+", "12345") is True
    assert safe_fullmatch(r"\d+", "12a45") is False
    assert safe_search(r"\d+", "abc 99 def") is True


def test_safe_match_kills_a_catastrophic_pattern():
    evil = r"(a+)+$"
    payload = "a" * 40 + "!"
    with pytest.raises(MatchTimeout):
        safe_search(evil, payload, timeout=0.5)


def test_timeout_message_explains_the_cause():
    with pytest.raises(MatchTimeout, match="backtracking"):
        safe_search(r"(a+)+$", "a" * 40 + "!", timeout=0.3)


@pytest.mark.parametrize(
    "pattern",
    [
        # A bounded but *wide* inner repeat is still combinatorial to split:
        # this one is unusable at two dozen characters, and a rule that looked
        # only for `maximum is None` called it safe.
        r"^([1-9]{1}[0-9]{0,7})+((,[1-9]{1}[0-9]{0,7}){0,1})+$",
        r"(a{0,7})+",
        r"(a{1,2})+",
        # Every atom here is a single optional, which CPython handles alone —
        # but six in a row give the outer `+` combinatorially many ways to
        # divide the same text, and it hangs.
        r"^((\.)?([a-zA-Z0-9_-]?)(\.)?([a-zA-Z0-9_-]?)(\.)?)+$",
    ],
)
def test_variable_width_bodies_are_flagged(pattern):
    result = screen(pattern, empirical=False)
    assert result.risk is Risk.EXPONENTIAL, result.reason


@pytest.mark.parametrize("pattern", [r"(\d?)*", r"(a?)*", r"(a{2}){2}", r"a{2}a{2}"])
def test_fixed_and_single_optional_bodies_are_not_flagged(pattern):
    """`(\\d?)*` varies by one optional character and CPython's empty-loop
    guard keeps it linear; `(a{2}){2}` divides exactly one way."""
    result = screen(pattern, empirical=False)
    assert result.risk is Risk.SAFE, result.reason
