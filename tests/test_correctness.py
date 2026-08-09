import pytest

from regexbench import Task, Verdict, check, evaluate


def test_perfect_pattern_scores_everything():
    task = Task(positives=["123", "456"], negatives=["12", "abcd", ""])
    result = check(r"\d{3}", task)
    assert result.perfect
    assert result.passed == 5
    assert result.accuracy == 1.0


def test_false_negatives_and_positives_are_named():
    task = Task(positives=["123", "45"], negatives=["abc", "6789"])
    result = check(r"\d{3}", task)
    assert result.false_negatives == ["45"]
    assert result.false_positives == []
    assert not result.perfect


def test_over_permissive_pattern_reports_false_positives():
    task = Task(positives=["123"], negatives=["abc"])
    result = check(r".*", task)
    assert result.false_positives == ["abc"]


def test_uncompilable_pattern_scores_zero_with_an_error():
    task = Task(positives=["a"], negatives=["b"])
    result = check(r"(unclosed", task)
    assert result.passed == 0
    assert result.error is not None
    assert not result


def test_matching_is_full_not_partial():
    task = Task(positives=[], negatives=["123abc"])
    assert check(r"\d{3}", task).perfect, "fullmatch semantics: \\d{3} must reject 123abc"


def test_task_requires_examples():
    with pytest.raises(ValueError):
        Task(positives=[], negatives=[])


def test_evaluate_combines_all_three_axes():
    task = Task(
        positives=["123-4567"],
        negatives=["123-456", "abc"],
        reference=r"[0-9]{3}-[0-9]{4}",
    )
    report = evaluate(r"[0-9][0-9][0-9]-[0-9]{4}", task)

    assert report.correctness.perfect
    assert not report.safety.risk.is_vulnerable
    assert report.equivalence is not None
    assert report.equivalence.verdict is Verdict.EQUIVALENT
    assert report.usable


def test_correct_but_dangerous_pattern_is_not_usable():
    task = Task(positives=["aaa"], negatives=["b"])
    report = evaluate(r"(a+)+", task)
    assert report.correctness.perfect
    assert report.safety.risk.is_vulnerable
    assert not report.usable, "a correct pattern that can hang is still not shippable"


def test_examples_do_not_overrule_a_proven_difference():
    """The failure mode this package exists to catch.

    `#[0-9a-f]{6}` rejects uppercase hex, so it is the wrong pattern — but the
    task's examples happen to be lowercase, so every one of them passes. The
    reference settles it, and a couple of examples that did not happen to ask
    must not overrule that.
    """
    task = Task(
        positives=["#a1b2c3"],
        negatives=["#xyz"],
        reference=r"#[0-9a-fA-F]{6}",
    )
    report = evaluate(r"#[0-9a-f]{6}", task)

    assert report.correctness.perfect, "it does pass every example it was given"
    assert report.equivalence.verdict is Verdict.DIFFERENT
    assert not report.usable
    assert report.equivalence.witness is not None


def test_an_unanswerable_comparison_does_not_condemn_a_passing_pattern():
    """UNSUPPORTED means the engine could not answer, not that the answer is no."""
    task = Task(positives=["ab"], negatives=["ba"], reference=r"(?<=a+)b")
    report = evaluate("ab", task)

    assert report.equivalence.verdict is Verdict.UNSUPPORTED
    assert report.correctness.perfect
    assert report.usable, "examples are the evidence left when equivalence cannot answer"
