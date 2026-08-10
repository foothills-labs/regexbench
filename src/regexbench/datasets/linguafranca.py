"""LinguaFranca — regexes people wrote, with no prompts and no reference.

> Davis, Michael IV, Coghlan, Servant and Lee, *Why Aren't Regular Expressions
> a Lingua Franca? An Empirical Study on the Re-use and Portability of Regular
> Expressions*, ESEC/FSE 2019.
> <https://github.com/VTLeeLab/LinguaFranca-FSE19>

Three corpora, distributed under `data/`:

* **Production** (537,806 unique patterns) — statically extracted from 193,524
  projects in eight languages. `data/production-regexes/uniq-regexes-8.json`.
* **Stack Overflow** (495,135) — entities that look like regexes, scraped from
  regex posts. `data/internet-regexes/stackoverflow/data/`.
* **RegExLib** (3,838) — the same, from regexlib.com.
  `data/internet-regexes/regexlib/data/`.

These are not benchmarks and this loader does not return :class:`~regexbench.Task`
objects. A record here is a pattern and nothing else — no description to
generate from, no gold answer to score against — so there is no `pass@k` and
no `dfa-eq@k` to compute. What they are good for is the other direction:
checking the *engine* rather than a model, by asking whether its automaton
agrees with `re` on patterns nobody chose to be representative.

That is worth doing because a curated corpus exercises the constructs its
curator thought of. Running these three found six wrong-answer bugs that
762 hand-picked references and a random pattern generator had both missed.
See :func:`~regexbench.crosscheck`, or the `regexbench crosscheck` command.

Both record shapes are JSON lines. Production records are
``{"pattern": ..., "useCount_registry_to_nModules": {"pypi": 3, ...}}``; the
internet-sources records are ``{"patterns": [...], "uri": ...}``. Neither is
redistributed here — download them and pass the path.
"""

from __future__ import annotations

import json
from pathlib import Path

__all__ = ["load_linguafranca"]


def load_linguafranca(
    path: str | Path,
    *,
    registry: str | None = None,
    limit: int | None = None,
) -> list[str]:
    """Load the unique patterns from a LinguaFranca corpus file.

    `registry` keeps only patterns used by at least one module in that
    registry — `"pypi"`, `"npm"`, `"cpan"`, `"maven"`, `"packagist"`,
    `"crates.io"`, `"godoc"`, `"rubygems"`. It applies to the production
    corpus, which records the counts; the internet-sources files carry no
    registry, so asking for one there yields nothing.

    Restricting to `"pypi"` is the honest choice when the results will be
    stated in terms of Python: the other seven languages' regexes are written
    against engines with syntax `re` does not have, and counting those as
    "unsupported" would measure the dialect gap rather than this engine.

    `limit` stops after that many patterns, for a quick pass over a corpus
    that is half a million lines long.

    Returns patterns in file order, deduplicated, with non-string records
    dropped — the production corpus has a handful where the extractor emitted
    a boolean.
    """
    file = Path(path)
    if not file.is_file():
        raise FileNotFoundError(f"{file} is not a file")

    patterns: list[str] = []
    seen: set[str] = set()
    with file.open(encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{file}:{number}: not JSON — {exc}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"{file}:{number}: expected a JSON object")

            if registry is not None and not _uses(record, registry):
                continue
            for pattern in _patterns(record):
                if pattern not in seen:
                    seen.add(pattern)
                    patterns.append(pattern)
                    if limit is not None and len(patterns) >= limit:
                        return patterns
    return patterns


def _patterns(record: dict) -> list[str]:
    """The pattern strings in one record, whichever shape it is."""
    if "patterns" in record:  # internet sources: a post can quote several
        return [p for p in record["patterns"] if isinstance(p, str)]
    pattern = record.get("pattern")
    return [pattern] if isinstance(pattern, str) else []


def _uses(record: dict, registry: str) -> bool:
    counts = record.get("useCount_registry_to_nModules")
    return isinstance(counts, dict) and bool(counts.get(registry))
