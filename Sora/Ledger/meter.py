"""The recording hook.

Exactly one function in this repo writes an `llm_call` record: `metered()`.
`llm/client.py:chat()` routes every request through it, so any code path -
baseline agent, memory compaction, guardrail check, LLM-as-judge, or a
framework calling on our behalf - is metered without opting in. If a future
framework bypasses `llm.client`, that is a finding, and the way to notice it is
that its calls are missing from out/trace.jsonl.

What a caller can add on top (all optional, all ambient so it survives being
called from inside somebody else's loop):

    from Sora import Ledger

    with Ledger.call_context(category="compaction", turn=4, cache_lane="chat"):
        ...                                   # any llm.client.chat(...) inside

    Ledger.chat(messages, tools=TOOLS, category="chat")   # explicit form

Per-component token counts come from annotating messages with `_component`
(see tokenizer.component_of); the meter strips those keys before the request
leaves the process.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import itertools
import os
import threading
import time
import uuid
from datetime import datetime, timezone

from . import config, pricing, tokenizer, trace

CATEGORIES = ("chat", "compaction", "memory", "guardrails", "tools", "judge", "eval", "other")

_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar("sora_ledger_ctx", default={})
_seq = itertools.count(1)
_lock = threading.Lock()

# Last prompt rendering per cache lane, for the byte-identical-prefix metric
# (ASSIGNMENT.md 5.4). In-process only; report_stats recomputes the same number
# from the trace so cross-process runs are covered too.
_last_prompt: dict[str, tuple[str, str]] = {}   # lane -> (call_id, rendered prompt)

_totals: dict = {
    "calls": 0, "errors": 0, "prompt_tokens": 0, "completion_tokens": 0,
    "cached_prompt_tokens": 0, "usd": 0.0, "unpriced_calls": 0, "latency_ms": 0.0,
}


@contextlib.contextmanager
def call_context(**fields):
    """Attach metadata to every metered call made inside the block.

    Recognised: category, tags (list), tag (str, overrides SORA_LEDGER_TAG),
    session_id, turn, cache_lane, note, plus anything else, which lands in
    `extra`. Nests: inner values win, outer values survive.
    """
    merged = dict(_ctx.get())
    merged.update({k: v for k, v in fields.items() if v is not None})
    token = _ctx.set(merged)
    try:
        yield merged
    finally:
        _ctx.reset(token)


def current_context() -> dict:
    return dict(_ctx.get())


def _clean_messages(messages):
    """Strip ledger-only annotations (`_component`, ...) before sending."""
    cleaned = []
    for msg in messages or []:
        if isinstance(msg, dict) and any(str(k).startswith("_") for k in msg):
            cleaned.append({k: v for k, v in msg.items() if not str(k).startswith("_")})
        else:
            cleaned.append(msg)
    return cleaned


def _usage_dict(resp) -> dict:
    usage = getattr(resp, "usage", None)
    if usage is None:
        return {}
    for attr in ("model_dump", "dict", "to_dict"):
        fn = getattr(usage, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                pass
    return dict(usage) if isinstance(usage, dict) else {}


def _provider_tokens(usage: dict) -> dict:
    details = usage.get("prompt_tokens_details") or {}
    if not isinstance(details, dict):
        details = {}
    completion_details = usage.get("completion_tokens_details") or {}
    if not isinstance(completion_details, dict):
        completion_details = {}
    return {
        "prompt": usage.get("prompt_tokens"),
        "completion": usage.get("completion_tokens"),
        "total": usage.get("total_tokens"),
        "cached_prompt": details.get("cached_tokens") or 0,
        "reasoning": completion_details.get("reasoning_tokens") or 0,
    }


def _wants_usage_accounting() -> bool:
    base = os.environ.get("LLM_BASE_URL", "")
    return config.usage_accounting() and "openrouter" in base


def _tool_names(tools):
    names = []
    for t in tools or []:
        fn = (t or {}).get("function", {}) if isinstance(t, dict) else {}
        names.append(fn.get("name", "?"))
    return names


def _response_message(resp):
    try:
        return resp.choices[0].message
    except Exception:
        return None


def _tool_calls_of(msg):
    out = []
    for tc in getattr(msg, "tool_calls", None) or []:
        fn = getattr(tc, "function", None)
        out.append({
            "id": getattr(tc, "id", None),
            "name": getattr(fn, "name", None),
            "arguments": getattr(fn, "arguments", None),
        })
    return out


def metered(send, messages, tools=None, **kwargs):
    """Run one chat completion through `send` and record it.

    `send(messages, tools=..., **kwargs)` is the raw transport (normally
    llm.client._raw_chat). Errors are recorded and re-raised: a call that
    failed after the provider did work still shows up in the trace.
    """
    clean = _clean_messages(messages)

    if _wants_usage_accounting():
        extra_body = dict(kwargs.get("extra_body") or {})
        extra_body.setdefault("usage", {"include": True})
        kwargs["extra_body"] = extra_body

    if config.disabled():
        return send(clean, tools=tools, **kwargs)

    ctx = _ctx.get()
    model = kwargs.get("model") or _model_name()
    started = time.perf_counter()
    resp = error = None
    try:
        resp = send(clean, tools=tools, **kwargs)
        return resp
    except Exception as exc:                      # noqa: BLE001 - recorded, then re-raised
        error = {"type": type(exc).__name__, "message": str(exc)[:2000]}
        raise
    finally:
        latency_ms = (time.perf_counter() - started) * 1000.0
        try:
            _record(ctx, model, messages, clean, tools, kwargs, resp, error, latency_ms)
        except Exception as log_exc:              # never let logging break a run
            trace.write({
                "event": "ledger_error", "ts": _now(),
                "message": "failed to record call: %r" % (log_exc,),
            })


def _model_name() -> str:
    try:
        from llm.client import model_name

        return model_name()
    except Exception:
        return os.environ.get("LLM_MODEL", "unknown")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _record(ctx, model, raw_messages, clean_messages, tools, kwargs, resp, error, latency_ms):
    call_id = uuid.uuid4().hex[:16]
    category = ctx.get("category", "chat")
    lane = ctx.get("cache_lane", category)

    # --- local token accounting (annotations live on the un-stripped messages)
    local = tokenizer.count_prompt(raw_messages, tools=tools, model=model)
    msg = _response_message(resp)
    text = getattr(msg, "content", None) or ""
    tool_calls = _tool_calls_of(msg)
    completion_local = tokenizer.count_text(
        text + "".join((tc.get("arguments") or "") + (tc.get("name") or "") for tc in tool_calls),
        model,
    )

    usage = _usage_dict(resp)
    ptok = _provider_tokens(usage)

    # --- cache: provider-reported, plus the byte-identical prefix fallback
    rendered = tokenizer.render_prompt(clean_messages, tools)
    prev_id, prev_rendered = _last_prompt.get(lane, (None, None))
    prefix = {"prev_call_id": prev_id, "prompt_chars": len(rendered)}
    if prev_rendered is not None:
        shared = tokenizer.common_prefix_len(prev_rendered, rendered)
        prefix.update({
            "identical_chars": shared,
            "prev_prompt_chars": len(prev_rendered),
            "identical_ratio": round(shared / len(rendered), 6) if rendered else None,
            "identical_tokens": tokenizer.count_text(rendered[:shared], model),
        })
    _last_prompt[lane] = (call_id, rendered)

    prompt_tokens = ptok.get("prompt")
    cached = ptok.get("cached_prompt") or 0
    cache = {
        "lane": lane,
        "provider_cached_tokens": cached,
        "provider_hit_rate": round(cached / prompt_tokens, 6) if prompt_tokens else None,
        "provider_reports_cache": "prompt_tokens_details" in usage,
        "prefix": prefix,
    }

    cost = pricing.resolve(
        model,
        usage.get("cost"),
        prompt_tokens if prompt_tokens is not None else local["total"],
        ptok.get("completion") if ptok.get("completion") is not None else completion_local,
        cached,
    )
    if isinstance(usage.get("cost_details"), dict):
        cost["cost_details"] = usage["cost_details"]

    ceiling = config.context_ceiling()
    budget_basis = prompt_tokens if prompt_tokens is not None else local["total"]

    record = {
        "event": "llm_call",
        "ts": _now(),
        "seq": next(_seq),
        "call_id": call_id,
        "run_id": config.run_id(),
        "session_id": ctx.get("session_id", config.session_id()),
        "turn": ctx.get("turn"),
        "category": category,
        "tag": ctx.get("tag", config.tag()),
        "tags": list(ctx.get("tags") or []),
        "note": ctx.get("note"),
        "provider": {
            "base_url": os.environ.get("LLM_BASE_URL", ""),
            "model_requested": model,
            "model_served": getattr(resp, "model", None),
            "response_id": getattr(resp, "id", None),
            "finish_reason": _finish_reason(resp),
        },
        "sampling": _sampling(kwargs, model, tools),
        "request": {
            "messages": clean_messages,
            "tools": tools,
            "prompt_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
            "prompt_chars": len(rendered),
        },
        "response": {
            "text": text,
            "tool_calls": tool_calls,
            "error": error,
        },
        "tokens": {
            "provider": ptok,
            "local": {
                "prompt": local["total"],
                "completion": completion_local,
                "total": local["total"] + completion_local,
                "encoding": tokenizer.effective_encoding_name(model),
                "encoding_requested": tokenizer.encoding_name(model),
                "method": tokenizer.METHOD,
            },
            "components": local["components"],
            "reconciliation": tokenizer.reconcile(
                {"prompt": local["total"], "completion": completion_local}, ptok
            ),
            "ceiling": ceiling,
            "headroom": ceiling - budget_basis if budget_basis is not None else None,
        },
        "cache": cache,
        "cost": cost,
        "latency": {"total_ms": round(latency_ms, 2), "ttft_ms": None},
    }
    trace.write(record)
    _accumulate(record)
    return record


def _finish_reason(resp):
    try:
        return resp.choices[0].finish_reason
    except Exception:
        return None


_SAMPLING_KEYS = (
    "temperature", "top_p", "top_k", "max_tokens", "max_completion_tokens",
    "presence_penalty", "frequency_penalty", "seed", "stop", "n",
    "response_format", "tool_choice", "parallel_tool_calls", "stream",
    "reasoning_effort", "extra_body",
)


def _sampling(kwargs, model, tools):
    out = {"model": model, "tools_exposed": _tool_names(tools)}
    for key in _SAMPLING_KEYS:
        if key in kwargs:
            out[key] = kwargs[key]
    # Record the defaults we did NOT send, so "why is she different today" is
    # answerable from the trace alone rather than from the provider's docs.
    out["unset_defaults"] = [k for k in ("temperature", "top_p", "max_tokens", "seed")
                             if k not in kwargs]
    return out


def _accumulate(record):
    with _lock:
        _totals["calls"] += 1
        if record["response"]["error"]:
            _totals["errors"] += 1
        prov = record["tokens"]["provider"]
        loc = record["tokens"]["local"]
        _totals["prompt_tokens"] += prov.get("prompt") or loc.get("prompt") or 0
        _totals["completion_tokens"] += prov.get("completion") or loc.get("completion") or 0
        _totals["cached_prompt_tokens"] += prov.get("cached_prompt") or 0
        _totals["latency_ms"] += record["latency"]["total_ms"]
        usd = record["cost"].get("usd")
        if usd is None:
            _totals["unpriced_calls"] += 1
        else:
            _totals["usd"] += usd


def totals() -> dict:
    """Running totals for THIS process (report_stats reads the whole trace)."""
    with _lock:
        out = dict(_totals)
    out["usd"] = round(out["usd"], 6)
    prompt = out["prompt_tokens"]
    out["cache_hit_rate"] = round(out["cached_prompt_tokens"] / prompt, 4) if prompt else None
    return out


def summary_line() -> str:
    t = totals()
    hit = "n/a" if t["cache_hit_rate"] is None else "%.1f%%" % (100 * t["cache_hit_rate"])
    return (
        "[ledger] %d calls | %d prompt + %d completion tokens | cache hit %s | $%.6f%s | %s"
        % (t["calls"], t["prompt_tokens"], t["completion_tokens"], hit, t["usd"],
           " (%d unpriced)" % t["unpriced_calls"] if t["unpriced_calls"] else "",
           config.trace_path())
    )


@contextlib.contextmanager
def tool_span(name: str, args=None, category: str = "tools"):
    """Record a tool invocation into the same trace file.

    ASSIGNMENT.md 8.1 wants tool calls AND their results with latency. The
    model's *request* to call a tool is already on the llm_call record; this
    captures what actually came back, how long it took, and whether it blew up.

        with Ledger.tool_span("web_search", args) as span:
            span.result = search(...)
    """
    span = _ToolSpan(name, args, category)
    started = time.perf_counter()
    try:
        yield span
    except Exception as exc:                      # noqa: BLE001 - recorded, then re-raised
        span.error = {"type": type(exc).__name__, "message": str(exc)[:2000]}
        raise
    finally:
        if not config.disabled():
            ctx = _ctx.get()
            result = span.result if isinstance(span.result, str) else _as_text(span.result)
            trace.write({
                "event": "tool_call",
                "ts": _now(),
                "seq": next(_seq),
                "call_id": uuid.uuid4().hex[:16],
                "run_id": config.run_id(),
                "session_id": ctx.get("session_id", config.session_id()),
                "turn": ctx.get("turn"),
                "category": span.category,
                "tag": ctx.get("tag", config.tag()),
                "tags": list(ctx.get("tags") or []),
                "tool": {
                    "name": span.name,
                    "args": span.args,
                    "tool_call_id": span.tool_call_id,
                    "result": result,
                    "result_chars": len(result or ""),
                    "result_tokens_local": tokenizer.count_text(result or "", _model_name()),
                    "ok": span.error is None,
                    "error": span.error,
                },
                "latency": {"total_ms": round((time.perf_counter() - started) * 1000.0, 2)},
            })


def _as_text(value):
    import json

    if value is None:
        return ""
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return repr(value)


class _ToolSpan:
    __slots__ = ("name", "args", "category", "result", "error", "tool_call_id")

    def __init__(self, name, args, category):
        self.name = name
        self.args = args
        self.category = category
        self.result = None
        self.error = None
        self.tool_call_id = None
