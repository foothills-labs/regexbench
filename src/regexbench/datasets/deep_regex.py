"""KB13 and NL-RX — natural language paired with a gold regular expression.

Three corpora share one format, distributed together at
https://github.com/nicholaslocascio/deep-regex under ``datasets/``:

* **KB13** (824 pairs) — Kushman and Barzilay, 2013.
* **NL-RX-Synth** (10,000 pairs) — descriptions generated from a grammar.
* **NL-RX-Turk** (10,000 pairs) — the same regexes, re-described by workers.

Each is a directory holding two parallel line-aligned files: ``src.txt`` with
the descriptions and ``targ.txt`` with the expressions. They are not
redistributed here; download them and pass the directory.

Two things about these corpora decide how they have to be loaded.

**They carry no worked examples.** A record is a description and a gold
pattern, nothing else, so the only available score is equivalence against the
reference — which is exactly the DFA-EQ metric this literature reports.

**The patterns are dk.brics.automaton syntax, not Python.** They use ``&`` for
intersection and ``~`` for complement, which `re` silently compiles as
literals. Hence :data:`~regexbench.Dialect.BRICS`.

Records whose pattern uses ``\\b`` are loaded like any other and come back
UNSUPPORTED from the equivalence engine rather than being dropped here — 48.9%
of KB13 and 19.0% of NL-RX. A benchmark that quietly discards the hard half of
its corpus reports a number nobody can interpret.
"""

from __future__ import annotations

from pathlib import Path

from ..types import Dialect, Semantics, Task

__all__ = ["load_deep_regex"]


def load_deep_regex(
    directory: str | Path,
    *,
    name: str | None = None,
    source: str = "src.txt",
    target: str = "targ.txt",
) -> list[Task]:
    """Load a KB13 or NL-RX corpus from `directory`.

    `name` prefixes the task names, defaulting to the directory's own name so
    that `KB13/7` and `NL-RX-Turk/7` stay distinguishable when corpora are
    scored together.
    """
    root = Path(directory)
    if not root.is_dir():
        raise NotADirectoryError(f"{root} is not a directory")

    prompts = _read_lines(root / source)
    patterns = _read_lines(root / target)
    if len(prompts) != len(patterns):
        raise ValueError(
            f"{root}: {source} has {len(prompts)} lines but {target} has {len(patterns)} — "
            f"these files must be line-aligned"
        )

    label = name if name is not None else root.name
    return [
        Task(
            prompt=prompt,
            reference=pattern,
            name=f"{label}/{index}",
            semantics=Semantics.FULLMATCH,
            dialect=Dialect.BRICS,
        )
        for index, (prompt, pattern) in enumerate(zip(prompts, patterns, strict=True))
    ]


def _read_lines(path: Path) -> list[str]:
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found")
    # splitlines rather than readlines: the published files have no trailing
    # newline, and readlines would not change the count but would keep the
    # separators.
    return path.read_text(encoding="utf-8").splitlines()
