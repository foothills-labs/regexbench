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
        reference=r"\d{3}-\d{4}",
    )
    report = evaluate(r"[0-9]{3}-[0-9]{4}", task)

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
