"""dk.brics.automaton syntax, which the regex-generation corpora are written in.

KB13 and NL-RX gold patterns use `&` for intersection and `~` for complement —
the language the field's own DFA-equality tooling reads. Both are ordinary
literals in Python, and `^`/`$` are literals in dk.brics but anchors in Python,
so nothing about the misreading is loud: `re` compiles `(a)&(b)` without
complaint, as a five-character string.
"""

import pytest

from regexbench import Dialect, Semantics, Task, Verdict, check, equivalent, evaluate

BRICS = Dialect.BRICS


def test_intersection_keeps_only_what_both_branches_match():
    assert equivalent("([0-9])&([0-4])", "[0-4]", dialect=BRICS).verdict is Verdict.EQUIVALENT


def test_intersection_of_disjoint_branches_is_the_empty_language():
    # Nothing is both 'a' and 'b', so `a&b` matches nothing — and `#` is how
    # dk.brics spells the empty language.
    assert equivalent("(a)&(b)", "#", dialect=BRICS).verdict is Verdict.EQUIVALENT


def test_complement_matches_everything_the_inner_pattern_does_not():
    result = equivalent("~(a)", "a", dialect=BRICS)
    assert result.verdict is Verdict.DIFFERENT


def test_complement_is_an_involution():
    assert equivalent("~(~(a))", "a", dialect=BRICS).verdict is Verdict.EQUIVALENT


def test_de_morgan_holds():
    assert equivalent(
        "~((a)|(b))", "(~(a))&(~(b))", dialect=BRICS
    ).verdict is Verdict.EQUIVALENT


def test_intersection_binds_tighter_than_alternation():
    # `a|b&c` is `a|(b&c)`, and `b&c` is empty, so the whole thing is just `a`.
    assert equivalent("a|b&c", "a", dialect=BRICS).verdict is Verdict.EQUIVALENT


def test_complement_binds_tighter_than_concatenation():
    # `~ab` is `(~a)b`, not `~(ab)`.
    assert equivalent("~ab", "(~(a))b", dialect=BRICS).verdict is Verdict.EQUIVALENT
    assert equivalent("~ab", "~(ab)", dialect=BRICS).verdict is Verdict.DIFFERENT


def test_any_string_and_empty_language_atoms():
    assert equivalent("@", ".*", dialect=BRICS).verdict is Verdict.EQUIVALENT
    assert equivalent("#", "(a)&(b)", dialect=BRICS).verdict is Verdict.EQUIVALENT


def test_anchors_are_literal_characters_in_brics():
    # dk.brics has no anchors, so `^` is just a caret.
    assert equivalent("^", "\\^", dialect=BRICS).verdict is Verdict.EQUIVALENT


def test_python_dialect_still_reads_the_operators_as_literals():
    # The default dialect must not change: `&` is a literal ampersand.
    assert equivalent("a&b", "a\\&b").verdict is Verdict.EQUIVALENT


def test_the_two_dialects_disagree_about_the_same_text():
    """The whole reason the dialect is explicit rather than sniffed."""
    # `a\&b` is the literal three-character string in either dialect, so it
    # is a fixed point to measure both readings against.
    pattern = "(a)&(b)"
    assert equivalent(pattern, r"a\&b").verdict is Verdict.EQUIVALENT
    assert equivalent(pattern, r"a\&b", dialect=BRICS).verdict is Verdict.DIFFERENT
    # Python reads it as a literal; dk.brics reads it as an empty intersection.
    assert equivalent(pattern, "#", dialect=BRICS).verdict is Verdict.EQUIVALENT


def test_word_boundaries_are_read_as_boundaries_not_as_a_literal_b():
    """A deliberate, documented deviation from the dk.brics spec.

    dk.brics escapes `\b` to the literal character 'b'. The corpora that use
    this dialect mean a word boundary, and their paired descriptions say so —
    KB13 glosses `.*\b[A-Za-z]*er\b.*` as "lines using words ending in 'er'",
    which the literal reading does not describe at all. The intent wins.
    """
    boundary = r".*\bab\b.*"
    assert equivalent(boundary, r".*ab.*", dialect=BRICS).verdict is Verdict.DIFFERENT
    # Under the literal-'b' reading it would instead mean this, which it does not.
    assert equivalent(boundary, r".*bab b.*", dialect=BRICS).verdict is Verdict.DIFFERENT
    # A word in isolation is found; the same letters inside a longer word are not.
    assert equivalent(boundary, r".*ab.*", dialect=BRICS).witness is not None


def test_brics_patterns_are_not_executed_by_re():
    task = Task(positives=["a"], negatives=["b"], dialect=BRICS)
    result = check("(a)&(b)", task)
    assert result.error is not None
    assert not result.perfect


def test_an_example_free_brics_task_reports_no_correctness_signal():
    task = Task(reference="(a)|(b)", dialect=BRICS)
    result = check("a|b", task)
    assert result.total == 0
    assert result.error is None


def test_brics_tasks_are_scored_by_equivalence_and_not_screened():
    task = Task(reference="((a)|(b))*", dialect=BRICS)
    report = evaluate("(a|b)*", task)
    assert report.equivalence.verdict is Verdict.EQUIVALENT
    assert not report.safety.risk.is_vulnerable
    assert "not screened" in report.safety.reason
    assert report.usable


def test_real_corpus_patterns_round_trip():
    """Verbatim gold patterns from KB13 and NL-RX-Turk."""
    for pattern in (
        r"~(.*e.*)",
        r"((dog)|(truck)){5,}",
        r"((dog)&(truck)).*([0-9]).*",
        r"(([AEIOUaeiou])*).*(([a-z])&([A-Z])).*",
        r"((dog).*(truck).*)|(~(ring))",
    ):
        assert equivalent(pattern, pattern, dialect=BRICS).verdict is Verdict.EQUIVALENT


@pytest.mark.parametrize(
    "left,right",
    [
        ("(.*[AEIOU].*)&(.*[0-9].*)", "(.*[0-9].*)&(.*[AEIOU].*)"),  # commutative
        ("((a)&(b))&(c)", "(a)&((b)&(c))"),  # associative
        ("(a)&(a)", "a"),  # idempotent
    ],
)
def test_intersection_algebra(left: str, right: str):
    assert equivalent(left, right, dialect=BRICS).verdict is Verdict.EQUIVALENT


def test_search_semantics_composes_with_the_brics_dialect():
    # The corpora are full-match, but the two settings are independent knobs
    # and must not interfere.
    result = equivalent("(a)&(a)", "a", dialect=BRICS, semantics=Semantics.SEARCH)
    assert result.verdict is Verdict.EQUIVALENT
