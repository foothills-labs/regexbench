"""The batch scoring harness."""

from __future__ import annotations

import pytest

from regexbench import Dialect, Semantics, Task, pass_at_k, run


class TestPassAtK:
    @pytest.mark.parametrize(
        "n,c,k,expected",
        [
            (1, 1, 1, 1.0),
            (1, 0, 1, 0.0),
            (2, 1, 1, 0.5),
            (5, 1, 1, 0.2),
            (5, 1, 5, 1.0),  # k draws from 5 always includes the one success
            (5, 0, 5, 0.0),
            (10, 2, 2, 17 / 45),  # 1 - C(8,2)/C(10,2)
        ],
    )
    def test_known_values(self, n, c, k, expected):
        assert pass_at_k(n, c, k) == pytest.approx(expected)

    def test_it_is_monotone_in_k(self):
        scores = [pass_at_k(10, 3, k) for k in range(1, 8)]
        assert scores == sorted(scores)

    @pytest.mark.parametrize("n,c,k", [(0, 0, 1), (5, 6, 1), (5, -1, 1), (5, 1, 0)])
    def test_impossible_inputs_are_refused(self, n, c, k):
        with pytest.raises(ValueError):
            pass_at_k(n, c, k)

    @pytest.mark.parametrize("n,c,k", [(1, 0, 3), (2, 0, 3), (2, 2, 3), (1, 1, 2)])
    def test_short_tasks_are_refused_not_credited(self, n, c, k):
        # Regression for the 0.4.0 defect: n < k fell into the
        # every-k-subset-contains-a-success shortcut and returned 1.0 --
        # a task with one failed sample scored a full pass at k=3. The
        # estimator is undefined below k, so this must raise, successes
        # and failures alike.
        with pytest.raises(ValueError):
            pass_at_k(n, c, k)

    def test_the_shortcut_still_fires_at_n_equals_k(self):
        assert pass_at_k(3, 1, 3) == 1.0  # any 3 of 3 includes the success
        assert pass_at_k(3, 0, 3) == 0.0


def digits_task(name: str = "digits") -> Task:
    return Task(
        positives=["123"],
        negatives=["abc"],
        reference=r"[0-9]+",
        name=name,
    )


class TestRun:
    def test_predictions_can_be_aligned_by_position(self):
        report = run([digits_task()], [r"[0-9][0-9]*"])
        assert report.tasks == 1
        assert report.answered == 1
        assert report.dfa_eq_at(1) == 1.0

    def test_predictions_can_be_keyed_by_task_name(self):
        tasks = [digits_task("a"), digits_task("b")]
        report = run(tasks, {"b": r"[0-9][0-9]*", "a": r"[0-9]+"})
        assert report.dfa_eq_at(1) == 1.0

    def test_a_task_with_no_prediction_stays_in_the_denominator(self):
        tasks = [digits_task("a"), digits_task("b")]
        report = run(tasks, {"a": r"\d+"})
        assert report.tasks == 2
        assert report.answered == 1
        assert report.unanswered == 1

    def test_mismatched_lengths_are_refused(self):
        with pytest.raises(ValueError, match="2 predictions for 1 tasks"):
            run([digits_task()], ["a", "b"])

    def test_unknown_prediction_names_are_refused(self):
        with pytest.raises(ValueError, match="not in the task set"):
            run([digits_task("a")], {"a": "x", "typo": "y"})

    def test_named_keying_requires_names(self):
        with pytest.raises(ValueError, match="has no name"):
            run([Task(positives=["a"])], {"whatever": "x"})

    def test_several_samples_per_task(self):
        # Two of four candidates are right, so pass@1 is 50%.
        report = run([digits_task()], [[r"[0-9]+", r"[0-9][0-9]*", "nope", "[a-z]+"]])
        assert report.pass_at(1) == pytest.approx(0.5)
        assert report.pass_at(4) == pytest.approx(1.0)


class TestMetrics:
    def test_exact_match_understates_what_dfa_eq_measures(self):
        """The headline claim of the package, as a number."""
        report = run([digits_task()], [r"[0-9][0-9]*"])
        assert report.exact_at(1) == 0.0
        assert report.dfa_eq_at(1) == 1.0

    def test_a_metric_with_no_qualifying_task_is_none_not_zero(self):
        # KB13-shaped: a reference, no examples. pass@k is not answerable.
        task = Task(reference=r"[0-9]+", name="eq-only")
        report = run([task], [r"[0-9][0-9]*"])
        assert report.pass_at(1) is None
        assert report.dfa_eq_at(1) == 1.0

    def test_an_undecidable_comparison_counts_against_dfa_eq(self):
        # Textually different, both non-regular: nothing can decide this. (The
        # same pattern twice would be settled by reflexivity, not analysis.)
        task = Task(reference=r"(a)\1", name="backref")
        report = run([task], [r"(b)\1"])
        assert report.dfa_eq_at(1) == 0.0, "undecidable must not be scored as equivalent"
        assert report.undecided == 1, "...but it must be reported separately"

    def test_vulnerable_at_k_counts_redos(self):
        task = Task(positives=["aaa"], negatives=["b"], name="redos")
        report = run([task], [r"(a+)+"])
        assert report.vulnerable_at(1) == 1.0
        assert report.pass_at(1) == 1.0, "the pattern is correct..."
        assert report.usable_at(1) == 0.0, "...and still not shippable"

    def test_brics_tasks_are_not_counted_in_vulnerability(self):
        task = Task(reference="(a)|(b)", name="k/0", dialect=Dialect.BRICS)
        report = run([task], ["a|b"])
        assert report.vulnerable_at(1) is None, "brics patterns are never screened"
        assert report.dfa_eq_at(1) == 1.0

    def test_search_semantics_reaches_the_harness(self):
        task = Task(
            positives=["x123y"],
            negatives=["xy"],
            reference=r"[0-9]{3}",
            name="s",
            semantics=Semantics.SEARCH,
        )
        report = run([task], [r"[0-9]{3}"])
        assert report.pass_at(1) == 1.0
        assert report.dfa_eq_at(1) == 1.0


class TestResilience:
    def test_one_bad_pattern_does_not_abort_the_sweep(self):
        tasks = [digits_task("a"), digits_task("b")]
        report = run(tasks, {"a": "(unclosed", "b": r"[0-9][0-9]*"})
        assert report.tasks == 2
        assert report.dfa_eq_at(1) == pytest.approx(0.5)

    def test_workers_do_not_change_the_answer(self):
        tasks = [digits_task(f"t{i}") for i in range(6)]
        predictions = [r"[0-9][0-9]*" if i % 2 else "nope" for i in range(6)]
        serial = run(tasks, predictions)
        parallel = run(tasks, predictions, workers=4)
        assert serial.summary() == parallel.summary()

    def test_progress_is_reported_once_per_task(self):
        seen = []
        run(
            [digits_task(f"t{i}") for i in range(3)],
            ["a", "b", "c"],
            progress=lambda done, total: seen.append((done, total)),
        )
        assert seen == [(1, 3), (2, 3), (3, 3)]


class TestPresentation:
    def test_summary_is_plain_data(self):
        report = run([digits_task()], [r"[0-9][0-9]*"], name="my-model")
        summary = report.summary(ks=(1,))
        assert summary["name"] == "my-model"
        assert summary["tasks"] == 1
        assert summary["metrics"]["dfa-eq@1"] == 1.0

    def test_table_marks_unanswerable_metrics_as_not_applicable(self):
        report = run([Task(reference=r"[0-9]+", name="eq-only")], [r"[0-9][0-9]*"])
        table = report.table()
        assert "n/a" in table, "pass@1 is undefined here and must not read as 0%"
        assert "dfa-eq@1" in table

    def test_table_names_the_model_and_flags_undecidable_tasks(self):
        report = run([Task(reference=r"(a)\1", name="backref")], [r"(b)\1"], name="m")
        table = report.table()
        assert table.startswith("m")
        assert "undecidable" in table


class TestDecidedSubset:
    def test_the_two_dfa_eq_readings_differ_by_the_undecidable_tasks(self):
        """A flawless model, half of whose tasks the engine cannot analyze."""
        tasks = [
            Task(reference=r"[0-9]+", name="ok1"),
            Task(reference=r"[0-9]+", name="ok2"),
            Task(reference=r"(a)\1", name="undecidable1"),
            Task(reference=r"(a)\1", name="undecidable2"),
        ]
        report = run(tasks, [r"[0-9][0-9]*", r"[0-9][0-9]*", r"(b)\1", r"(b)\1"])

        assert report.undecided == 2
        assert report.dfa_eq_at(1) == pytest.approx(0.5), "whole corpus: a lower bound"
        assert report.dfa_eq_decided_at(1) == 1.0, "decidable subset: the model was perfect"

    def test_both_readings_agree_when_everything_is_decidable(self):
        report = run([digits_task()], [r"[0-9][0-9]*"])
        assert report.dfa_eq_at(1) == report.dfa_eq_decided_at(1) == 1.0

    def test_the_decided_reading_is_none_when_nothing_is_decidable(self):
        report = run([Task(reference=r"(a)\1", name="x")], [r"(b)\1"])
        assert report.dfa_eq_at(1) == 0.0
        assert report.dfa_eq_decided_at(1) is None, "no decidable task to average over"

    def test_the_table_labels_which_reading_is_which(self):
        tasks = [Task(reference=r"\d+", name="ok"), Task(reference=r"(a)\1", name="no")]
        table = run(tasks, [r"[0-9][0-9]*", r"(a)\1"]).table()
        assert "dfa-eq@1 (decided)" in table
        assert "whole corpus" in table
        assert "model only" in table
