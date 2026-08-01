"""Loading your own tasks.

The published corpora are for comparing against published numbers. Scoring
your own model on your own problems needs a format you control, so this reads
a plain list of task objects — either a JSON array or one JSON object per line:

.. code-block:: json

    {"name": "us-phone",
     "prompt": "a US phone number",
     "reference": "\\\\d{3}-\\\\d{4}",
     "positives": ["555-1234"],
     "negatives": ["5551234"],
     "semantics": "fullmatch",
     "dialect": "python"}

Every field is optional except that a task needs either examples or a
reference. ``semantics`` defaults to ``fullmatch`` and ``dialect`` to
``python``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..types import Dialect, Semantics, Task

__all__ = ["load_tasks", "task_from_dict"]

_KNOWN = {"name", "prompt", "reference", "positives", "negatives", "semantics", "dialect"}


def task_from_dict(payload: dict[str, Any], *, where: str = "task") -> Task:
    """Build a Task from a decoded JSON object, rejecting unknown fields."""
    unknown = set(payload) - _KNOWN
    if unknown:
        raise ValueError(f"{where}: unknown field(s) {', '.join(sorted(unknown))}")
    try:
        semantics = Semantics(payload.get("semantics", "fullmatch"))
        dialect = Dialect(payload.get("dialect", "python"))
    except ValueError as exc:
        raise ValueError(f"{where}: {exc}") from exc

    try:
        return Task(
            positives=list(payload.get("positives", [])),
            negatives=list(payload.get("negatives", [])),
            prompt=payload.get("prompt", ""),
            reference=payload.get("reference"),
            name=payload.get("name", ""),
            semantics=semantics,
            dialect=dialect,
        )
    except ValueError as exc:
        raise ValueError(f"{where}: {exc}") from exc


def load_tasks(path: str | Path) -> list[Task]:
    """Load tasks from a JSON array or a JSON-lines file.

    The format is detected from the content rather than the extension, since
    both spellings are common and mislabelling one is easy.
    """
    text = Path(path).read_text(encoding="utf-8")
    records = _decode(text, str(path))
    return [
        task_from_dict(record, where=f"{path}: task {index}")
        for index, record in enumerate(records)
    ]


def _decode(text: str, where: str) -> list[dict[str, Any]]:
    stripped = text.lstrip()
    if stripped.startswith("["):
        records = json.loads(text)
        if not isinstance(records, list):  # pragma: no cover - json guarantees it
            raise ValueError(f"{where}: expected a JSON array")
        return records

    records = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{where}: line {number} is not valid JSON: {exc}") from exc
    return records
