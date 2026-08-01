"""Running a match without letting it hang the caller.

Python's ``re`` has no timeout, and a pathological pattern can spin for years
inside a single C-level call — no signal, no thread interrupt, and no way to
cancel it from Python. The only reliable escape is a separate process that can
be killed.

That process is a bare ``sys.executable -c`` child speaking JSON over pipes.
The obvious alternative, ``multiprocessing`` with the ``spawn`` start method,
re-imports the caller's ``__main__`` in the child, so a plain script that calls
into this module at import time crashes with a bootstrapping error instead of
matching anything. A child that imports nothing but the standard library has no
such coupling: it works the same from a script, a REPL, ``-c``, a notebook, a
thread, and a test runner.

It costs milliseconds per call, so use it where the pattern is untrusted:
model output, user input, anything generated. For patterns you wrote, use
``re`` directly. Scoring many strings against one pattern should go through
:func:`match_many`, which pays that cost once for the whole batch.
"""

from __future__ import annotations

import json
import queue
import re
import subprocess
import sys
import threading

__all__ = ["safe_fullmatch", "safe_search", "match_many", "MatchTimeout"]

_DEFAULT_TIMEOUT = 1.0

# Emits one JSON line per text, flushed immediately, so that a batch killed
# part way through still tells the parent exactly how far it got — which is
# what identifies the string that hung.
_CHILD = r"""
import json, re, sys

request = json.loads(sys.stdin.read())
try:
    compiled = re.compile(request["pattern"])
except Exception as exc:
    print(json.dumps({"error": "%s: %s" % (type(exc).__name__, exc)}), flush=True)
    raise SystemExit(0)

method = getattr(compiled, request["method"])
for index, text in enumerate(request["texts"]):
    try:
        matched = method(text) is not None
    except Exception as exc:
        print(json.dumps({"error": "%s: %s" % (type(exc).__name__, exc)}), flush=True)
        raise SystemExit(0)
    print(json.dumps({"index": index, "matched": matched}), flush=True)
"""


class MatchTimeout(TimeoutError):
    """The match exceeded its time budget and was killed."""


def _run_batch(
    pattern: str, texts: list[str], method: str, timeout: float
) -> tuple[dict[int, bool], bool]:
    """Match every text in one child process. Returns (results, ran_out_of_time).

    The budget is per text and enforced as *silence*: results stream back one
    line at a time, and going `timeout` seconds without a new line means the
    text currently being matched is stuck. Budgeting the batch as a whole
    instead — timeout times the number of texts — would let a pattern that
    hangs on everything burn a quadratic amount of wall clock, since each
    retry after a kill starts a slightly shorter batch that also hangs.
    """
    process = subprocess.Popen(
        [sys.executable, "-c", _CHILD],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )

    lines: queue.Queue[str | None] = queue.Queue()

    def drain() -> None:
        for line in process.stdout:  # type: ignore[union-attr]
            lines.put(line)
        lines.put(None)

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()

    # The child reads stdin to EOF before matching anything, so this cannot
    # deadlock against its output.
    request = json.dumps({"pattern": pattern, "method": method, "texts": texts})
    try:
        process.stdin.write(request)  # type: ignore[union-attr]
        process.stdin.close()  # type: ignore[union-attr]
    except BrokenPipeError:  # pragma: no cover - child died before reading
        pass

    results: dict[int, bool] = {}
    failure: str | None = None
    exhausted = False
    died = False

    while len(results) < len(texts):
        try:
            line = lines.get(timeout=timeout)
        except queue.Empty:
            exhausted = True
            break
        if line is None:  # the child exited
            died = True
            break
        try:
            message = json.loads(line)
        except json.JSONDecodeError:  # pragma: no cover - child emits JSON only
            continue
        if "error" in message:
            failure = message["error"]
            break
        results[message["index"]] = message["matched"]

    if process.poll() is None:
        process.kill()
    process.wait()

    if failure is not None:
        raise re.error(failure)
    if died and len(results) < len(texts):
        # A child that exits early without saying why is a real anomaly, not a
        # set of failed examples. Reporting it as the latter would quietly
        # turn a crash into a score.
        raise RuntimeError(
            f"match worker exited after {len(results)} of {len(texts)} results"
        )
    return results, exhausted


def match_many(
    pattern: str,
    texts: list[str],
    *,
    method: str = "fullmatch",
    timeout: float = _DEFAULT_TIMEOUT,
    resume: bool = True,
) -> list[bool | None]:
    """Match `pattern` against every text, one child process for the batch.

    Returns one entry per text: True or False, or None where matching that
    text ran out of time. `timeout` is the budget for a single text, so a
    batch of ten with a one second timeout may take ten seconds — but a batch
    that behaves takes one process start in total, not ten.

    `resume=False` stops at the first text that times out and leaves the rest
    None. Use it when one timeout already answers the question — the ReDoS
    probe stops as soon as any attack string hangs — since resuming means
    waiting out the budget again for every remaining text.

    Raises ``re.error`` if the pattern does not compile.
    """
    outcomes: list[bool | None] = [None] * len(texts)
    pending = list(range(len(texts)))

    while pending:
        batch = [texts[index] for index in pending]
        results, exhausted = _run_batch(pattern, batch, method, timeout)
        for offset, matched in results.items():
            outcomes[pending[offset]] = matched
        if not exhausted or not resume:
            break
        # The first text with no answer is the one still running when the
        # budget expired. Record it as a timeout and resume after it, so a
        # single pathological string cannot mask the rest of the batch.
        stuck = next((offset for offset in range(len(batch)) if offset not in results), None)
        if stuck is None:  # pragma: no cover - a full batch cannot be exhausted
            break
        pending = pending[stuck + 1 :]

    return outcomes


def _run_one(pattern: str, text: str, method: str, timeout: float) -> bool:
    matched = match_many(pattern, [text], method=method, timeout=timeout)[0]
    if matched is None:
        raise MatchTimeout(
            f"matching took longer than {timeout}s — pattern is likely vulnerable "
            f"to catastrophic backtracking"
        )
    return matched


def safe_fullmatch(pattern: str, text: str, timeout: float = _DEFAULT_TIMEOUT) -> bool:
    """Full-match `text` against `pattern`, raising MatchTimeout past `timeout`."""
    return _run_one(pattern, text, "fullmatch", timeout)


def safe_search(pattern: str, text: str, timeout: float = _DEFAULT_TIMEOUT) -> bool:
    """Search `text` for `pattern`, raising MatchTimeout past `timeout`."""
    return _run_one(pattern, text, "search", timeout)
