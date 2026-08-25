"""The one output: out/trace.jsonl.

Everything the ledger records goes through `write()` - one JSON object per
line, append-only, flushed per record so a crash mid-eval keeps every call that
already happened (the flood turn in session 3 is designed to provoke exactly
that). Opened per write rather than held open, so several processes replaying
different sessions can append to the same file.

Retention/PII: we log verbatim messages on purpose right now - debugging an
agent from a redacted trace is guesswork. Before this goes anywhere near real
users it needs a redaction pass at this choke point (one function, one place)
plus a retention policy; noted in the package docstring as future work.
"""
from __future__ import annotations

import json
import threading
from typing import Any, Iterator

from . import config

_LOCK = threading.Lock()


def write(record: dict) -> None:
    path = config.trace_path()
    line = json.dumps(record, ensure_ascii=False, default=_fallback)
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()


def _fallback(obj: Any) -> Any:
    """Never let an unserialisable object cost us a trace line."""
    for attr in ("model_dump", "dict", "to_dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                pass
    return repr(obj)


def read(path=None) -> Iterator[dict]:
    """Yield trace records. Skips malformed lines (a half-written final line
    after a kill -9 should not break reporting)."""
    path = path or config.trace_path()
    try:
        fh = open(path, "r", encoding="utf-8")
    except FileNotFoundError:
        return
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue
