"""Running a match without letting it hang the caller.

Python's ``re`` has no timeout, and a pathological pattern can spin for years
inside a single C-level call — no signal, no thread interrupt, and no way to
cancel it from Python. The only reliable escape is a separate process that can
be killed.

That costs milliseconds per call, so use it where the pattern is untrusted:
model output, user input, anything generated. For patterns you wrote, use
``re`` directly.
"""

from __future__ import annotations

import multiprocessing as mp
import re

__all__ = ["safe_fullmatch", "safe_search", "MatchTimeout"]

_DEFAULT_TIMEOUT = 1.0


class MatchTimeout(TimeoutError):
    """The match exceeded its time budget and was killed."""


def _worker(pattern: str, text: str, method: str, out) -> None:  # pragma: no cover
    try:
        compiled = re.compile(pattern)
        result = getattr(compiled, method)(text)
        out.send(("ok", result is not None))
    except Exception as exc:  # noqa: BLE001 - relayed to the parent verbatim
        out.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        out.close()


def _run(pattern: str, text: str, method: str, timeout: float) -> bool:
    parent, child = mp.Pipe(duplex=False)
    # "spawn" keeps this usable from threads and on platforms without fork.
    ctx = mp.get_context("spawn")
    process = ctx.Process(target=_worker, args=(pattern, text, method, child), daemon=True)
    process.start()
    child.close()

    try:
        if not parent.poll(timeout):
            raise MatchTimeout(
                f"matching took longer than {timeout}s — pattern is likely vulnerable "
                f"to catastrophic backtracking"
            )
        status, payload = parent.recv()
    except EOFError as exc:
        raise RuntimeError("match worker died without returning a result") from exc
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
            if process.is_alive():  # pragma: no cover - terminate normally suffices
                process.kill()
        parent.close()

    if status == "error":
        raise re.error(payload)
    return payload


def safe_fullmatch(pattern: str, text: str, timeout: float = _DEFAULT_TIMEOUT) -> bool:
    """Full-match `text` against `pattern`, raising MatchTimeout past `timeout`."""
    return _run(pattern, text, "fullmatch", timeout)


def safe_search(pattern: str, text: str, timeout: float = _DEFAULT_TIMEOUT) -> bool:
    """Search `text` for `pattern`, raising MatchTimeout past `timeout`."""
    return _run(pattern, text, "search", timeout)
