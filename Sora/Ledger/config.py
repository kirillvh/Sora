"""Ledger configuration. Everything is env-overridable so eval runs can be
re-tagged without touching code.

    SORA_TRACE_PATH        where the single trace lives   (default out/trace.jsonl)
    SORA_LEDGER_TAG        experiment tag stamped on every call  (default "baseline")
    SORA_SESSION_ID        session label stamped on every call   (default "adhoc")
    SORA_RUN_ID            run label (default: generated per process)
    SORA_CONTEXT_CEILING   token ceiling used for the headroom column (default 8000)
    SORA_TOKENIZER         tiktoken encoding name (default: derived from model)
    SORA_LEDGER_DISABLED   "1" disables recording (calls still work)
    SORA_LEDGER_USAGE_ACCOUNTING  "0" stops asking OpenRouter for cost in usage
"""
import os
import pathlib
import uuid

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

DEFAULT_TRACE_PATH = REPO_ROOT / "out" / "trace.jsonl"
DEFAULT_CONTEXT_CEILING = 8000  # ASSIGNMENT.md section 5: hard 8k ceiling per session turn

_RUN_ID = os.environ.get("SORA_RUN_ID") or uuid.uuid4().hex[:12]


def trace_path() -> pathlib.Path:
    return pathlib.Path(os.environ.get("SORA_TRACE_PATH", str(DEFAULT_TRACE_PATH)))


def tag() -> str:
    """Experiment tag. This is the before/after knob for the cache-layout A/B
    (ASSIGNMENT.md 5.4): run once with SORA_LEDGER_TAG=before, change the
    layout, run again with =after, then `report_stats --compare-cache`."""
    return os.environ.get("SORA_LEDGER_TAG", "baseline")


def session_id() -> str:
    return os.environ.get("SORA_SESSION_ID", "adhoc")


def run_id() -> str:
    return _RUN_ID


def context_ceiling() -> int:
    try:
        return int(os.environ.get("SORA_CONTEXT_CEILING", DEFAULT_CONTEXT_CEILING))
    except ValueError:
        return DEFAULT_CONTEXT_CEILING


def disabled() -> bool:
    return os.environ.get("SORA_LEDGER_DISABLED", "") in ("1", "true", "True")


def usage_accounting() -> bool:
    return os.environ.get("SORA_LEDGER_USAGE_ACCOUNTING", "1") not in ("0", "false", "False")
