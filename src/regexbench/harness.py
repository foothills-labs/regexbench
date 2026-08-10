"""Scoring many patterns against many tasks, and aggregating the result.

`evaluate()` answers a question about one pattern. A benchmark asks it about
thousands and needs the answers added up in a way that means something.

The metrics here are the ones this literature reports, so that a number
produced by `regexbench` can be put next to a published one:

* **pass@k** — functional correctness against worked examples.
* **dfa-eq@k** — semantic equivalence to the reference, the standard metric in
  regex generation, where exact match is known to be inadequate.
* **vulnerable@k** — the ReDoS counterpart from Re(gEx|DoS)Eval.
* **exact@k** — string equality, reported because it is what naive scoring
  measures and the gap to dfa-eq is the whole argument for this package.
* **usable@k** — correct *and* safe, which is the only one that answers
  "could I ship this".

All five use the unbiased estimator from the Codex paper: drawing *n* samples
per task and counting *c* successes,

    pass@k = 1 - C(n - c, k) / C(n, k)

averaged over tasks. With n == k it reduces to "at least one success in k",
which is what most people mean, but it stays unbiased when more samples are
drawn than are being scored.

Two things are deliberately *not* smoothed over:

**A metric with no qualifying task is None, not zero.** KB13 ships no examples,
so pass@k over KB13 is undefined. Reporting 0% there would read as total
failure of a model that was never asked the question.

**An undecidable comparison counts against dfa-eq.** A candidate whose
equivalence comes back UNSUPPORTED or UNDECIDABLE is scored as not equivalent,
so dfa-eq@k is a lower bound over the whole corpus rather than an average over
the subset that happened to be analyzable. `undecided` reports how many tasks
that affects, which is the number you need to interpret the score.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .correctness import evaluate
from .types import Dialect, Report, Task, Verdict

__all__ = [
    "CandidateResult",
    "SuiteReport",
    "TaskResult",
    "pass_at_k",
    "run",
]


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased estimate that at least one of `k` draws succeeds.

    `n` samples were generated and `c` of them succeeded. Computed as a
    product rather than with binomial coefficients, which overflow and lose
    precision well before benchmark-sized numbers do.
    """
    if k <= 0:
        raise ValueError(f"k must be positive, got {k}")
    if n <= 0:
        raise ValueError(f"n must be positive, got {n}")
    if c < 0 or c > n:
        raise ValueError(f"c must be between 0 and n={n}, got {c}")
    if n - c < k:
        return 1.0
    estimate = 1.0
    for i in range(n - c + 1, n + 1):
        estimate *= 1.0 - k / i
    return 1.0 - estimate


@dataclass(frozen=True)
class CandidateResult:
    """One pattern a model produced for one task."""

    pattern: str
    report: Report | None = None
    error: str | None = None

    @property
    def correct(self) -> bool:
        return self.report is not None and self.report.correctness.perfect

    @property
    def equivalent(self) -> bool:
        if self.report is None or self.report.equivalence is None:
            return False
        return self.report.equivalence.verdict is Verdict.EQUIVALENT

    @property
    def undecided(self) -> bool:
        """Equivalence could not be computed, rather than computed as False."""
        if self.report is None or self.report.equivalence is None:
            return False
        return self.report.equivalence.verdict in (Verdict.UNSUPPORTED, Verdict.UNDECIDABLE)

    @property
    def vulnerable(self) -> bool:
        return self.report is not None and self.report.safety.risk.is_vulnerable

    @property
    def usable(self) -> bool:
        return self.report is not None and self.report.usable


@dataclass(frozen=True)
class TaskResult:
    """Every candidate a model produced for one task."""

    task: Task
    candidates: tuple[CandidateResult, ...] = field(default_factory=tuple)

    @property
    def samples(self) -> int:
        return len(self.candidates)

    @property
    def exact_matches(self) -> int:
        reference = self.task.reference
        if reference is None:
            return 0
        return sum(1 for candidate in self.candidates if candidate.pattern == reference)

    @property
    def undecided(self) -> bool:
        """No candidate produced a usable equivalence verdict."""
        return bool(self.candidates) and all(
            candidate.undecided for candidate in self.candidates
        )


@dataclass(frozen=True)
class SuiteReport:
    """Aggregated results for one model over one set of tasks."""

    results: tuple[TaskResult, ...]
    name: str = ""

    @property
    def tasks(self) -> int:
        return len(self.results)

    @property
    def answered(self) -> int:
        """Tasks the model produced at least one pattern for."""
        return sum(1 for result in self.results if result.samples)

    @property
    def unanswered(self) -> int:
        return self.tasks - self.answered

    @property
    def errors(self) -> int:
        return sum(
            1
            for result in self.results
            for candidate in result.candidates
            if candidate.error is not None
        )

    @property
    def undecided(self) -> int:
        """Tasks where equivalence could not be decided for any candidate.

        These count as failures in dfa_eq_at, so this is the size of the gap
        between the reported score and what a complete engine could report.
        """
        return sum(1 for result in self.results if result.undecided)

    def pass_at(self, k: int = 1) -> float | None:
        """Functional correctness, over tasks that carry examples."""
        return self._estimate(
            k,
            lambda result: result.task.has_examples,
            lambda result: sum(1 for c in result.candidates if c.correct),
        )

    def dfa_eq_at(self, k: int = 1) -> float | None:
        """Semantic equivalence, over tasks that carry a reference."""
        return self._estimate(
            k,
            lambda result: result.task.reference is not None,
            lambda result: sum(1 for c in result.candidates if c.equivalent),
        )

    def dfa_eq_decided_at(self, k: int = 1) -> float | None:
        """Semantic equivalence over only the tasks the engine could decide.

        The companion to `dfa_eq_at`, and the two answer different questions.
        `dfa_eq_at` asks how much of the corpus was verified correct, so an
        engine limit counts against the model. This asks how much of what could
        be checked was correct, so it measures the model alone.

        On Re(gEx|DoS)Eval the spread is the engine's coverage: only 82.5% of
        the references parse under the search semantics the corpus is scored
        with, so on the other 17.5% a candidate that is not textually
        identical to its reference comes back undecidable and counts as a
        failure under the first reading. Neither number is wrong and neither
        is sufficient, which is why both are reported.

        A task counts as decided when at least one of its candidates produced a
        verdict — undecidable candidates within a decided task still count as
        failures, since something comparable was available and this was not it.
        """
        return self._estimate(
            k,
            lambda result: result.task.reference is not None and not result.undecided,
            lambda result: sum(1 for c in result.candidates if c.equivalent),
        )

    def exact_at(self, k: int = 1) -> float | None:
        """String equality with the reference — the metric this package argues against."""
        return self._estimate(
            k,
            lambda result: result.task.reference is not None,
            lambda result: result.exact_matches,
        )

    def vulnerable_at(self, k: int = 1) -> float | None:
        """Chance that at least one of k patterns is a ReDoS liability.

        Unlike the others, lower is better. Only Python-dialect tasks qualify:
        a dk.brics pattern is destined for an automaton, where backtracking
        blowup does not exist, so it is never screened.
        """
        return self._estimate(
            k,
            lambda result: result.task.dialect is Dialect.PYTHON,
            lambda result: sum(1 for c in result.candidates if c.vulnerable),
        )

    def usable_at(self, k: int = 1) -> float | None:
        """Right and safe together, which is the only shippable standard."""
        return self._estimate(
            k,
            lambda result: True,
            lambda result: sum(1 for c in result.candidates if c.usable),
        )

    def _estimate(
        self,
        k: int,
        qualifies: Callable[[TaskResult], bool],
        successes: Callable[[TaskResult], int],
    ) -> float | None:
        scores = [
            pass_at_k(result.samples, successes(result), k)
            for result in self.results
            if qualifies(result) and result.samples
        ]
        # A metric nobody was asked is undefined, not zero.
        return sum(scores) / len(scores) if scores else None

    def summary(self, ks: Sequence[int] = (1,)) -> dict[str, object]:
        """Everything the table shows, as plain data."""
        metrics: dict[str, float | None] = {}
        for k in ks:
            metrics[f"pass@{k}"] = self.pass_at(k)
            metrics[f"dfa-eq@{k}"] = self.dfa_eq_at(k)
            metrics[f"dfa-eq@{k} (decided)"] = self.dfa_eq_decided_at(k)
            metrics[f"exact@{k}"] = self.exact_at(k)
            metrics[f"usable@{k}"] = self.usable_at(k)
            metrics[f"vulnerable@{k}"] = self.vulnerable_at(k)
        return {
            "name": self.name,
            "tasks": self.tasks,
            "answered": self.answered,
            "unanswered": self.unanswered,
            "undecided": self.undecided,
            "errors": self.errors,
            "metrics": metrics,
        }

    def table(self, ks: Sequence[int] = (1,)) -> str:
        """A readable summary. `n/a` means the corpus cannot answer that metric."""
        lines = []
        if self.name:
            lines.append(self.name)
        lines.append(
            f"{self.tasks} tasks, {self.answered} answered"
            + (f", {self.unanswered} with no prediction" if self.unanswered else "")
        )
        summary = self.summary(ks)
        metrics: dict[str, float | None] = summary["metrics"]  # type: ignore[assignment]
        width = max(len(label) for label in metrics)
        notes = {"vulnerable": "  (lower is better)"}
        for label, value in metrics.items():
            shown = "n/a" if value is None else f"{value:.1%}"
            if label.startswith("vulnerable"):
                note = notes["vulnerable"]
            elif label.endswith("(decided)"):
                note = "  (engine limits excluded — model only)"
            elif label.startswith("dfa-eq"):
                note = "  (whole corpus — a lower bound)"
            else:
                note = ""
            lines.append(f"  {label:<{width}}  {shown:>6}{note}")
        if self.undecided:
            lines.append(
                f"  {self.undecided} task(s) undecidable — counted against dfa-eq, "
                f"excluded from dfa-eq (decided)"
            )
        if self.errors:
            lines.append(f"  {self.errors} candidate(s) failed to evaluate")
        return "\n".join(lines)


def run(
    tasks: Sequence[Task],
    predictions: Mapping[str, str | Sequence[str]] | Sequence[str | Sequence[str]],
    *,
    name: str = "",
    timeout: float = 1.0,
    workers: int = 1,
    progress: Callable[[int, int], None] | None = None,
) -> SuiteReport:
    """Score `predictions` against `tasks`.

    Predictions are either a mapping from task name to the pattern (or list of
    sampled patterns) the model produced, or a sequence aligned with `tasks`.
    A task with no prediction is kept and reported as unanswered rather than
    dropped, so the denominator stays the corpus you asked about.

    `workers` runs tasks concurrently. The work is dominated by child
    processes, so threads help despite the GIL.
    """
    candidates = _align(tasks, predictions)
    total = len(tasks)
    done = 0

    def score(index: int) -> TaskResult:
        task = tasks[index]
        scored = []
        for pattern in candidates[index]:
            try:
                scored.append(CandidateResult(pattern, evaluate(pattern, task, timeout=timeout)))
            except Exception as exc:  # noqa: BLE001 - a sweep must survive one bad pattern
                scored.append(CandidateResult(pattern, error=f"{type(exc).__name__}: {exc}"))
        return TaskResult(task=task, candidates=tuple(scored))

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = []
            for result in pool.map(score, range(total)):
                results.append(result)
                done += 1
                if progress is not None:
                    progress(done, total)
    else:
        results = []
        for index in range(total):
            results.append(score(index))
            done += 1
            if progress is not None:
                progress(done, total)

    return SuiteReport(results=tuple(results), name=name)


def _align(
    tasks: Sequence[Task],
    predictions: Mapping[str, str | Sequence[str]] | Sequence[str | Sequence[str]],
) -> list[list[str]]:
    """Line predictions up with tasks, by name or by position."""
    if isinstance(predictions, Mapping):
        unnamed = [index for index, task in enumerate(tasks) if not task.name]
        if unnamed:
            raise ValueError(
                f"predictions are keyed by task name, but task {unnamed[0]} has no name"
            )
        names = {task.name for task in tasks}
        unknown = sorted(set(predictions) - names)
        if unknown:
            raise ValueError(
                f"predictions contain {len(unknown)} name(s) not in the task set, "
                f"first is {unknown[0]!r}"
            )
        return [_as_list(predictions.get(task.name, [])) for task in tasks]

    if len(predictions) != len(tasks):
        raise ValueError(
            f"got {len(predictions)} predictions for {len(tasks)} tasks — "
            f"pass a mapping keyed by task name if they are not aligned"
        )
    return [_as_list(entry) for entry in predictions]


def _as_list(entry: str | Sequence[str]) -> list[str]:
    if isinstance(entry, str):
        return [entry]
    return list(entry)
