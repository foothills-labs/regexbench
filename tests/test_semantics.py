"""Full-match versus search semantics.

Benchmarks disagree about what "matches" means, and the disagreement is
silent: a reference pattern written for `re.search` looks simply wrong when
scored with `re.fullmatch`. Re(gEx|DoS)Eval is the concrete case — its gold
patterns pass 100% of their own tests under search and 94% under full match.
"""

import pytest

from regexbench import Semantics, Task, Verdict, check, equivalent, evaluate


def test_search_semantics_accepts_a_substring_match():
    task = Task(positives=["123abc"], negatives=["abc"], semantics=Semantics.SEARCH)
    assert check(r"\d{3}", task).perfect


def test_full_match_semantics_rejects_the_same_substring():
    task = Task(positives=["123abc"], negatives=["abc"])
    result = check(r"\d{3}", task)
    assert result.false_negatives == ["123abc"]


def test_search_is_not_the_default():
    assert Task(positives=["a"]).semantics is Semantics.FULLMATCH


def test_unanchored_patterns_are_equivalent_under_search_only():
    # Searching for `a` and for `.*a.*` finds the same strings; full-matching
    # them does not.
    assert equivalent("a", ".*a.*", semantics=Semantics.SEARCH).verdict is Verdict.EQUIVALENT
    assert equivalent("a", ".*a.*").verdict is Verdict.DIFFERENT


def test_anchors_survive_the_search_reduction():
    # `^a` finds only strings starting with 'a'; `a` finds it anywhere.
    result = equivalent("^a", "a", semantics=Semantics.SEARCH)
    assert result.verdict is Verdict.DIFFERENT
    assert result.witness is not None and not result.witness.startswith("a")


def test_a_leading_anchor_binds_only_to_the_first_branch():
    # Regression: in `^a|b` the anchor constrains `a` and says nothing about
    # `b`, so the pattern as a whole is not anchored. Treating it as anchored
    # made it look equal to `^(a|b)`, which really does anchor both.
    result = equivalent("^a|b", "^(a|b)", semantics=Semantics.SEARCH)
    assert result.verdict is Verdict.DIFFERENT
    assert result.witness is not None and result.witness.endswith("b")


def test_a_trailing_anchor_binds_only_to_the_last_branch():
    result = equivalent("a|b$", "(a|b)$", semantics=Semantics.SEARCH)
    assert result.verdict is Verdict.DIFFERENT
    assert result.witness is not None and result.witness.startswith("a")


def test_a_fully_anchored_pattern_searches_like_a_full_match():
    task = Task(positives=["abc"], negatives=["aabc", "abcd"], semantics=Semantics.SEARCH)
    assert check("^abc$", task).perfect
    # ...and so it is *not* the same as searching for the bare pattern, which
    # would find `abc` inside `aabc` too.
    assert equivalent("^abc$", "abc", semantics=Semantics.SEARCH).verdict is Verdict.DIFFERENT


def test_evaluate_uses_the_task_semantics_for_equivalence():
    task = Task(
        positives=["x123y"],
        negatives=["xy"],
        reference=r"[0-9]{3}",
        semantics=Semantics.SEARCH,
    )
    report = evaluate("[0-9][0-9][0-9]", task)
    assert report.correctness.perfect
    assert report.equivalence.verdict is Verdict.EQUIVALENT
    assert report.usable


def test_a_task_may_carry_a_reference_and_no_examples():
    # KB13 and NL-RX ship a gold pattern with no worked examples at all.
    task = Task(reference=r"[0-9]+")
    assert not task.has_examples
    report = evaluate("[0-9][0-9]*", task)
    assert report.correctness.total == 0
    assert report.equivalence.verdict is Verdict.EQUIVALENT
    assert report.usable, "equivalence alone establishes an example-free task"


def test_an_example_free_task_that_differs_is_not_usable():
    report = evaluate("[0-9]", Task(reference=r"[0-9]+"))
    assert report.equivalence.verdict is Verdict.DIFFERENT
    assert not report.usable


def test_a_task_needs_something_to_score_against():
    with pytest.raises(ValueError):
        Task()
