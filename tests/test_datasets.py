"""Dataset adapters.

Fixtures mirror the published schemas exactly, including the details that bite:
RegexEval records are search-semantics and their examples live under `matches`
and `non_matches`; the deep-regex corpora are two line-aligned text files with
no trailing newline, carrying dk.brics patterns and no examples at all.
"""

from __future__ import annotations

import json

import pytest

from regexbench import Dialect, Semantics, Verdict, check, equivalent
from regexbench.datasets import (
    load_deep_regex,
    load_regexeval,
    load_tasks,
)

# Verbatim records from Re(gEx|DoS)Eval, including one whose reference only
# passes its own tests under search.
REGEXEVAL_RECORDS = [
    {
        "expression": "^\\d$",
        "raw_prompt": "Matches exactly 1 numeric digit (0-9).",
        "refined_prompt": 'Matches exactly 1 numeric digit (0-9).\nMatch examples:\n- "1"',
        "matches": ["1", "2", "0"],
        "non_matches": ["a", "324", "hello world"],
        "id": 1,
    },
    {
        "expression": "\\d{3}",
        "raw_prompt": "three digits somewhere in the line",
        "refined_prompt": 'three digits somewhere in the line\nMatch examples:\n- "x123y"',
        "matches": ["123", "x123y", "abc999"],
        "non_matches": ["12", "ab"],
        "id": 42,
    },
]

# Verbatim gold patterns from KB13 (src/targ line pairs).
KB13_SRC = [
    "lines which do not contain the letter 'e'.",
    "lines that contain only the letters 'agde'.",
    "lines using words  ending in 'er'.",
]
KB13_TARG = [
    "~(.*e.*)",
    "agde",
    ".*\\b[A-Za-z]*er\\b.*",
]


@pytest.fixture
def regexeval_file(tmp_path):
    path = tmp_path / "RegexEval.json"
    path.write_text(json.dumps(REGEXEVAL_RECORDS), encoding="utf-8")
    return path


@pytest.fixture
def kb13_dir(tmp_path):
    root = tmp_path / "KB13"
    root.mkdir()
    # No trailing newline, matching the published files.
    (root / "src.txt").write_text("\n".join(KB13_SRC), encoding="utf-8")
    (root / "targ.txt").write_text("\n".join(KB13_TARG), encoding="utf-8")
    return root


class TestRegexEval:
    def test_every_record_becomes_a_task(self, regexeval_file):
        tasks = load_regexeval(regexeval_file)
        assert len(tasks) == 2
        assert [task.name for task in tasks] == ["regexeval/1", "regexeval/42"]

    def test_fields_land_where_they_belong(self, regexeval_file):
        task = load_regexeval(regexeval_file)[0]
        assert task.reference == "^\\d$"
        assert task.positives == ["1", "2", "0"]
        assert task.negatives == ["a", "324", "hello world"]
        assert task.prompt == "Matches exactly 1 numeric digit (0-9)."

    def test_tasks_are_search_semantics(self, regexeval_file):
        assert all(task.semantics is Semantics.SEARCH for task in load_regexeval(regexeval_file))

    def test_the_reference_passes_its_own_tests(self, regexeval_file):
        """The check that catches a wrong semantics choice.

        `\\d{3}` matches "x123y" only under search. Loaded full-match, the
        dataset's own gold answer would be scored as failing.
        """
        for task in load_regexeval(regexeval_file):
            result = check(task.reference, task)
            assert result.perfect, (
                f"{task.name}: reference {task.reference!r} fails its own tests "
                f"({result.false_negatives} / {result.false_positives})"
            )

    def test_prompt_style_selects_the_wording(self, regexeval_file):
        raw = load_regexeval(regexeval_file, prompt="raw")[0]
        refined = load_regexeval(regexeval_file, prompt="refined")[0]
        assert "Match examples" not in raw.prompt
        assert "Match examples" in refined.prompt

    def test_an_unknown_prompt_style_is_refused(self, regexeval_file):
        with pytest.raises(ValueError, match="prompt must be one of"):
            load_regexeval(regexeval_file, prompt="verbose")

    def test_a_missing_field_names_the_record(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text(json.dumps([{"expression": "a", "id": 3}]), encoding="utf-8")
        with pytest.raises(ValueError, match="record 0 is missing"):
            load_regexeval(path)

    def test_a_non_list_file_is_refused(self, tmp_path):
        path = tmp_path / "wrong.json"
        path.write_text(json.dumps({"expression": "a"}), encoding="utf-8")
        with pytest.raises(ValueError, match="expected a JSON list"):
            load_regexeval(path)


class TestDeepRegex:
    def test_every_line_pair_becomes_a_task(self, kb13_dir):
        tasks = load_deep_regex(kb13_dir)
        assert len(tasks) == 3
        assert [task.name for task in tasks] == ["KB13/0", "KB13/1", "KB13/2"]

    def test_the_description_is_the_prompt_and_the_pattern_the_reference(self, kb13_dir):
        task = load_deep_regex(kb13_dir)[0]
        assert task.prompt == "lines which do not contain the letter 'e'."
        assert task.reference == "~(.*e.*)"

    def test_tasks_are_brics_and_full_match(self, kb13_dir):
        for task in load_deep_regex(kb13_dir):
            assert task.dialect is Dialect.BRICS
            assert task.semantics is Semantics.FULLMATCH

    def test_tasks_carry_no_examples(self, kb13_dir):
        assert not any(task.has_examples for task in load_deep_regex(kb13_dir))

    def test_the_gold_pattern_is_read_as_brics(self, kb13_dir):
        """`~(.*e.*)` is a complement, not four literal characters."""
        task = load_deep_regex(kb13_dir)[0]
        result = equivalent(task.reference, ".*e.*", dialect=task.dialect)
        assert result.verdict is Verdict.DIFFERENT
        # The complement of "contains an e" is "contains no e".
        assert equivalent(
            task.reference, "[^e]*", dialect=task.dialect
        ).verdict is Verdict.EQUIVALENT

    def test_no_record_is_dropped_at_load_time(self, kb13_dir):
        """A loader that drops the hard records reports an uninterpretable number."""
        tasks = load_deep_regex(kb13_dir)
        assert len(tasks) == 3

    def test_word_boundary_records_are_analyzable(self, kb13_dir):
        """`\\b` used to be refused, which cost KB13 half its corpus."""
        word_boundary = load_deep_regex(kb13_dir)[2]
        assert "\\b" in word_boundary.reference
        result = equivalent(word_boundary.reference, ".*", dialect=Dialect.BRICS)
        assert result.verdict is Verdict.DIFFERENT, "analyzed, not refused"

    def test_the_name_can_be_overridden(self, kb13_dir):
        tasks = load_deep_regex(kb13_dir, name="custom")
        assert tasks[0].name == "custom/0"

    def test_misaligned_files_are_refused(self, tmp_path):
        root = tmp_path / "Broken"
        root.mkdir()
        (root / "src.txt").write_text("one\ntwo", encoding="utf-8")
        (root / "targ.txt").write_text("a", encoding="utf-8")
        with pytest.raises(ValueError, match="line-aligned"):
            load_deep_regex(root)

    def test_a_missing_directory_is_refused(self, tmp_path):
        with pytest.raises(NotADirectoryError):
            load_deep_regex(tmp_path / "nope")

    def test_a_missing_file_is_refused(self, tmp_path):
        root = tmp_path / "Empty"
        root.mkdir()
        with pytest.raises(FileNotFoundError):
            load_deep_regex(root)


class TestCustomTasks:
    def test_a_json_array_loads(self, tmp_path):
        path = tmp_path / "tasks.json"
        path.write_text(
            json.dumps(
                [
                    {"name": "one", "positives": ["a"], "negatives": ["b"]},
                    {"name": "two", "reference": "x"},
                ]
            ),
            encoding="utf-8",
        )
        tasks = load_tasks(path)
        assert [task.name for task in tasks] == ["one", "two"]

    def test_json_lines_load(self, tmp_path):
        path = tmp_path / "tasks.jsonl"
        path.write_text(
            '{"name": "one", "positives": ["a"]}\n\n{"name": "two", "positives": ["b"]}\n',
            encoding="utf-8",
        )
        assert [task.name for task in load_tasks(path)] == ["one", "two"]

    def test_semantics_and_dialect_are_read_from_the_file(self, tmp_path):
        path = tmp_path / "tasks.jsonl"
        path.write_text(
            '{"reference": "a", "semantics": "search", "dialect": "brics"}\n',
            encoding="utf-8",
        )
        task = load_tasks(path)[0]
        assert task.semantics is Semantics.SEARCH
        assert task.dialect is Dialect.BRICS

    def test_defaults_are_python_full_match(self, tmp_path):
        path = tmp_path / "tasks.jsonl"
        path.write_text('{"positives": ["a"]}\n', encoding="utf-8")
        task = load_tasks(path)[0]
        assert task.semantics is Semantics.FULLMATCH
        assert task.dialect is Dialect.PYTHON

    def test_an_unknown_field_is_refused(self, tmp_path):
        path = tmp_path / "tasks.jsonl"
        path.write_text('{"positives": ["a"], "reference_regex": "x"}\n', encoding="utf-8")
        with pytest.raises(ValueError, match="unknown field"):
            load_tasks(path)

    def test_an_unknown_semantics_is_refused(self, tmp_path):
        path = tmp_path / "tasks.jsonl"
        path.write_text('{"positives": ["a"], "semantics": "partial"}\n', encoding="utf-8")
        with pytest.raises(ValueError, match="partial"):
            load_tasks(path)

    def test_an_empty_task_is_refused_with_its_position(self, tmp_path):
        path = tmp_path / "tasks.jsonl"
        path.write_text('{"positives": ["a"]}\n{"prompt": "nothing to score"}\n', encoding="utf-8")
        with pytest.raises(ValueError, match="task 1"):
            load_tasks(path)

    def test_malformed_json_names_the_line(self, tmp_path):
        path = tmp_path / "tasks.jsonl"
        path.write_text('{"positives": ["a"]}\n{oops\n', encoding="utf-8")
        with pytest.raises(ValueError, match="line 2"):
            load_tasks(path)
