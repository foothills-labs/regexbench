"""Running untrusted patterns in a killable child process."""

from __future__ import annotations

import re
import subprocess
import sys
import time

import pytest

from regexbench import MatchTimeout, safe_fullmatch, safe_search
from regexbench.execute import match_many

# 40 a's and a character that cannot match: exponential backtracking.
CATASTROPHIC = r"(a+)+$"
ATTACK = "a" * 40 + "!"


def test_matching_works_from_a_plain_script(tmp_path):
    """Regression: the child must not depend on the caller's __main__.

    With multiprocessing's spawn method the child re-imports the parent's main
    module, so a script calling this at import time died with a bootstrapping
    error — the very first thing anyone would write.
    """
    script = tmp_path / "user_script.py"
    script.write_text(
        "from regexbench import safe_fullmatch\n"
        "assert safe_fullmatch(r'\\d+', '123') is True\n"
        "print('ok')\n",
        encoding="utf-8",
    )
    finished = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=60
    )
    assert finished.returncode == 0, finished.stderr
    assert "ok" in finished.stdout


def test_full_match_and_search_differ():
    assert safe_fullmatch(r"\d+", "123") is True
    assert safe_fullmatch(r"\d+", "123abc") is False
    assert safe_search(r"\d+", "123abc") is True


def test_a_hanging_match_raises_rather_than_blocking():
    start = time.time()
    with pytest.raises(MatchTimeout):
        safe_fullmatch(CATASTROPHIC, ATTACK, timeout=0.5)
    assert time.time() - start < 20, "the timeout did not actually kill the child"


def test_an_uncompilable_pattern_raises_re_error():
    with pytest.raises(re.error):
        safe_fullmatch("(unclosed", "anything")


def test_match_many_returns_one_outcome_per_text():
    outcomes = match_many(r"\d+", ["1", "abc", "22"])
    assert outcomes == [True, False, True]


def test_match_many_preserves_order_and_length():
    texts = [str(index) if index % 2 else "x" for index in range(10)]
    outcomes = match_many(r"\d", texts)
    assert outcomes == [bool(index % 2) for index in range(10)]


def test_match_many_isolates_the_text_that_hangs():
    """One pathological string must not swallow the rest of the batch."""
    outcomes = match_many(CATASTROPHIC, ["b", ATTACK, "b"], timeout=0.5)
    assert outcomes[0] is False
    assert outcomes[1] is None, "the hanging text should be reported as a timeout"
    assert outcomes[2] is False, "matching should resume after the hanging text"


def test_match_many_uses_one_process_for_a_well_behaved_batch():
    """The batch API exists so a benchmark sweep is not one process per string."""
    texts = [str(index) for index in range(60)]
    start = time.time()
    outcomes = match_many(r"\d+", texts)
    batched = time.time() - start

    start = time.time()
    for text in texts[:6]:
        safe_fullmatch(r"\d+", text)
    six_singles = time.time() - start

    assert all(outcomes)
    assert batched < six_singles, (
        f"60 texts batched took {batched:.2f}s but 6 texts singly took "
        f"{six_singles:.2f}s — the batch is not being shared"
    )


def test_match_many_accepts_an_empty_batch():
    assert match_many(r"\d+", []) == []


def test_a_batch_that_hangs_everywhere_stays_linear():
    """Regression: the budget is per text, not per batch.

    Budgeting the whole batch at timeout x len(texts) makes a pattern that
    hangs on every string quadratic — each retry after a kill starts a
    slightly shorter batch that also hangs and also waits out its whole
    budget. Six strings at 0.3s cost about 1.8s linear and about 6.3s
    quadratic.
    """
    attacks = ["a" * 40 + "!"] * 6
    start = time.time()
    outcomes = match_many(CATASTROPHIC, attacks, timeout=0.3)
    elapsed = time.time() - start

    assert outcomes == [None] * 6
    assert elapsed < 4.0, f"took {elapsed:.1f}s — the budget is scaling with the batch"
