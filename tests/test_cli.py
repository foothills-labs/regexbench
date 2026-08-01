"""The command line interface."""

from __future__ import annotations

import json

import pytest

from regexbench.cli import main

REGEXEVAL_RECORDS = [
    {
        "expression": "\\d{3}",
        "raw_prompt": "three digits",
        "refined_prompt": "three digits, e.g. 123",
        "matches": ["123", "x123y"],
        "non_matches": ["ab"],
        "id": 7,
    },
    {
        "expression": "^a+$",
        "raw_prompt": "only a's",
        "refined_prompt": "only a's, e.g. aaa",
        "matches": ["a", "aaa"],
        "non_matches": ["b", "ab"],
        "id": 8,
    },
]


@pytest.fixture
def regexeval_file(tmp_path):
    path = tmp_path / "RegexEval.json"
    path.write_text(json.dumps(REGEXEVAL_RECORDS), encoding="utf-8")
    return path


class TestEq:
    def test_equivalent_patterns_exit_zero(self, capsys):
        assert main(["eq", "[0-9]+", r"\d+"]) == 0
        assert "equivalent" in capsys.readouterr().out

    def test_different_patterns_exit_one_with_a_witness(self, capsys):
        assert main(["eq", "a", "b"]) == 1
        out = capsys.readouterr().out
        assert "different" in out
        assert "witness" in out

    def test_search_flag_changes_the_question(self, capsys):
        assert main(["eq", "a", ".*a.*"]) == 1
        assert main(["eq", "--search", "a", ".*a.*"]) == 0

    def test_brics_flag_changes_the_dialect(self, capsys):
        # `(a)&(b)` is the literal "a&b" in Python and the empty language in
        # dk.brics, so the same comparison flips.
        assert main(["eq", "(a)&(b)", r"a\&b"]) == 0
        assert main(["eq", "--brics", "(a)&(b)", r"a\&b"]) == 1


class TestSafety:
    def test_a_safe_pattern_exits_zero(self, capsys):
        assert main(["safety", r"\d{3}"]) == 0
        assert "safe" in capsys.readouterr().out

    def test_a_vulnerable_pattern_exits_one(self, capsys):
        assert main(["safety", "--no-empirical", "(a+)+$"]) == 1
        assert "safe" not in capsys.readouterr().out.split("\n")[0]


class TestCheck:
    def test_a_perfect_pattern_exits_zero(self, tmp_path, capsys):
        task = tmp_path / "task.json"
        task.write_text(json.dumps({"positives": ["123"], "negatives": ["ab"]}), encoding="utf-8")
        assert main(["check", r"\d{3}", str(task)]) == 0
        assert "2/2" in capsys.readouterr().out

    def test_a_task_file_can_ask_for_search_semantics(self, tmp_path, capsys):
        task = tmp_path / "task.json"
        task.write_text(
            json.dumps({"positives": ["x123y"], "negatives": ["ab"], "semantics": "search"}),
            encoding="utf-8",
        )
        assert main(["check", r"\d{3}", str(task)]) == 0

    def test_failures_are_named(self, tmp_path, capsys):
        task = tmp_path / "task.json"
        task.write_text(json.dumps({"positives": ["12"], "negatives": []}), encoding="utf-8")
        assert main(["check", r"\d{3}", str(task)]) == 1
        assert "should match but does not" in capsys.readouterr().out


class TestRun:
    def test_use_reference_scores_a_corpus_against_itself(self, regexeval_file, capsys):
        assert main(["run", "regexeval", str(regexeval_file), "--use-reference", "--quiet"]) == 0
        out = capsys.readouterr().out
        assert "2 tasks, 2 answered" in out
        # The gold answers must pass their own tests — this is the check that
        # catches a corpus loaded with the wrong match semantics.
        assert "pass@1" in out
        assert "100.0%" in out

    def test_predictions_are_read_from_a_json_mapping(self, regexeval_file, tmp_path, capsys):
        predictions = tmp_path / "preds.json"
        predictions.write_text(
            json.dumps({"regexeval/7": "[0-9]{3}", "regexeval/8": "nope"}), encoding="utf-8"
        )
        assert (
            main(
                [
                    "run",
                    "regexeval",
                    str(regexeval_file),
                    "--predictions",
                    str(predictions),
                    "--quiet",
                ]
            )
            == 0
        )
        assert "2 tasks, 2 answered" in capsys.readouterr().out

    def test_the_summary_can_be_written_as_json(self, regexeval_file, tmp_path, capsys):
        out_path = tmp_path / "summary.json"
        main(
            [
                "run",
                "regexeval",
                str(regexeval_file),
                "--use-reference",
                "--json",
                str(out_path),
                "--k",
                "1",
                "--quiet",
            ]
        )
        summary = json.loads(out_path.read_text(encoding="utf-8"))
        assert summary["tasks"] == 2
        assert summary["metrics"]["pass@1"] == 1.0

    def test_limit_truncates_the_corpus(self, regexeval_file, capsys):
        args = ["run", "regexeval", str(regexeval_file), "--use-reference"]
        main([*args, "--limit", "1", "--quiet"])
        assert "1 tasks, 1 answered" in capsys.readouterr().out

    def test_predictions_are_required(self, regexeval_file, capsys):
        assert main(["run", "regexeval", str(regexeval_file), "--quiet"]) == 2
        assert "required" in capsys.readouterr().err

    def test_a_custom_task_file_can_be_run(self, tmp_path, capsys):
        tasks = tmp_path / "tasks.jsonl"
        tasks.write_text(
            '{"name": "t1", "reference": "\\\\d+", "positives": ["12"], "negatives": ["a"]}\n',
            encoding="utf-8",
        )
        assert main(["run", "tasks", str(tasks), "--use-reference", "--quiet"]) == 0
        assert "1 tasks, 1 answered" in capsys.readouterr().out


def test_limit_narrows_a_full_predictions_file(tmp_path, capsys):
    """--limit asks for a subset, so predictions for the cut tasks are expected.

    Without this the harness's unknown-name check makes --limit unusable with a
    real predictions file, which is exactly when you reach for it.
    """
    dataset = tmp_path / "RegexEval.json"
    dataset.write_text(json.dumps(REGEXEVAL_RECORDS), encoding="utf-8")
    predictions = tmp_path / "preds.json"
    predictions.write_text(
        json.dumps({"regexeval/7": "[0-9]{3}", "regexeval/8": "a+"}), encoding="utf-8"
    )
    exit_code = main(
        [
            "run", "regexeval", str(dataset),
            "--predictions", str(predictions),
            "--limit", "1", "--quiet",
        ]
    )
    assert exit_code == 0
    assert "1 tasks, 1 answered" in capsys.readouterr().out
