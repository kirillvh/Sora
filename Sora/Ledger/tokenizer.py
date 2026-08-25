"""Local token accounting.

ASSIGNMENT.md section 2: "if your tokenizer doesn't exactly match your model,
that is fine: name the tokenizer your budget numbers come from and reconcile it
against the provider-reported `usage` in your trace."

So: every number this module produces is labelled with the encoding it came
from (`encoding_name()`), every call records BOTH the local count and the
provider count, and `reconcile()` writes the delta into the trace line. The
aggregated reconciliation lives in `report_stats.py --reconcile`.

Counting method ("openai-chat-v1"): the documented OpenAI chat-completions
accounting for the gpt-4o family - 3 tokens of framing per message, 1 extra for
a `name` field, 3 tokens of assistant priming at the end. Tool schemas are
counted as their serialised JSON plus a flat per-tool overhead; that part is an
approximation and is the main expected source of drift (see reconcile()).
"""
from __future__ import annotations

import json
import os
from typing import Any, Iterable

_TOKENS_PER_MESSAGE = 3
_TOKENS_PER_NAME = 1
_REPLY_PRIMING = 3
_TOOL_OVERHEAD = 8       # per exposed function schema; approximate, see docstring
_TOOLS_PREAMBLE = 12     # flat cost of turning tools on at all; approximate

METHOD = "openai-chat-v1"

_ENCODINGS: dict[str, Any] = {}


def encoding_name(model: str | None = None) -> str:
    """Name the tokenizer our budget numbers come from."""
    override = os.environ.get("SORA_TOKENIZER")
    if override:
        return override
    model = (model or "").lower()
    if "gpt-4o" in model or "gpt-5" in model or "o1" in model or "o3" in model:
        return "o200k_base"
    if "gpt-4" in model or "gpt-3.5" in model:
        return "cl100k_base"
    # Non-OpenAI models on OpenRouter (Qwen, Llama, ...) do NOT use o200k_base.
    # We still count with it and let reconcile() expose the drift rather than
    # pretending we have the model's real tokenizer.
    return "o200k_base"


class _CharFallback:
    """Last-resort estimator when tiktoken is unavailable (offline box, etc.).

    Labelled distinctly in the trace so nobody mistakes it for a real count.
    """

    name = "heuristic-chars-4"

    def encode(self, text: str):
        return [0] * ((len(text) + 3) // 4)


def _get_encoding(name: str):
    enc = _ENCODINGS.get(name)
    if enc is None:
        try:
            import tiktoken

            enc = tiktoken.get_encoding(name)
        except Exception:  # tiktoken missing, or its vocab file unreachable
            enc = _CharFallback()
        _ENCODINGS[name] = enc
    return enc


def effective_encoding_name(model: str | None = None) -> str:
    """What we ACTUALLY counted with (may be the fallback, not the request)."""
    name = encoding_name(model)
    return getattr(_get_encoding(name), "name", name)


def count_text(text: str, model: str | None = None) -> int:
    if not text:
        return 0
    return len(_get_encoding(encoding_name(model)).encode(text))


def _content_to_text(content: Any) -> str:
    """Flatten a message `content` (str, or the multipart list form)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(part.get("text") or json.dumps(part, ensure_ascii=False))
            else:
                parts.append(str(part))
        return "\n".join(parts)
    return json.dumps(content, ensure_ascii=False)


def count_message(msg: dict, model: str | None = None) -> int:
    """Tokens for one chat message, framing included."""
    n = _TOKENS_PER_MESSAGE
    n += count_text(str(msg.get("role", "")), model)
    n += count_text(_content_to_text(msg.get("content")), model)
    if msg.get("name"):
        n += _TOKENS_PER_NAME + count_text(str(msg["name"]), model)
    if msg.get("tool_call_id"):
        n += count_text(str(msg["tool_call_id"]), model)
    for tc in msg.get("tool_calls") or []:
        fn = (tc or {}).get("function", {})
        n += count_text(str(fn.get("name", "")), model)
        n += count_text(str(fn.get("arguments", "")), model)
        n += _TOKENS_PER_NAME
    return n


def count_tools(tools: Iterable[dict] | None, model: str | None = None) -> int:
    """Approximate cost of the exposed function schemas."""
    tools = list(tools or [])
    if not tools:
        return 0
    total = _TOOLS_PREAMBLE
    for t in tools:
        total += _TOOL_OVERHEAD + count_text(json.dumps(t, ensure_ascii=False), model)
    return total


_ROLE_COMPONENT = {
    "system": "system",
    "developer": "system",
    "tool": "tool_output",
    "function": "tool_output",
    "user": "history",
    "assistant": "history",
}


def component_of(msg: dict) -> str:
    """Which context component a message belongs to.

    A message may carry an explicit `_component` key (e.g. "persona", "memory",
    "user_input"). The meter strips those keys before the request leaves the
    process, so annotating messages costs nothing at the API boundary. This is
    how the per-turn budget table in ASSIGNMENT.md section 5.1 gets its rows.
    """
    explicit = msg.get("_component")
    if explicit:
        return str(explicit)
    return _ROLE_COMPONENT.get(str(msg.get("role", "")), "history")


def count_prompt(messages: list[dict], tools=None, model: str | None = None) -> dict:
    """Local prompt accounting, broken down by context component.

    Returns {"total": int, "components": {name: tokens}}. The components always
    sum to `total` (message framing is charged to the message's own component,
    the trailing reply-priming to "overhead"), so a trace line can be audited
    without re-deriving anything.
    """
    components: dict[str, int] = {}
    for msg in messages:
        comp = component_of(msg)
        components[comp] = components.get(comp, 0) + count_message(msg, model)
    tool_tokens = count_tools(tools, model)
    if tool_tokens:
        components["tool_schemas"] = components.get("tool_schemas", 0) + tool_tokens
    components["overhead"] = components.get("overhead", 0) + _REPLY_PRIMING
    return {"total": sum(components.values()), "components": components}


def render_prompt(messages: list[dict], tools=None) -> str:
    """Deterministic flat rendering of a request, used for hashing and for the
    byte-identical-prefix cache metric.

    Tools first, then messages in order: that mirrors where a provider puts the
    tool schemas (stable prefix) relative to the conversation (volatile tail),
    which is exactly what the prefix metric is trying to measure.
    """
    parts = []
    if tools:
        parts.append("<|tools|>" + json.dumps(tools, ensure_ascii=False, sort_keys=True))
    for msg in messages:
        chunk = ["<|" + str(msg.get("role", "")) + "|>", _content_to_text(msg.get("content"))]
        if msg.get("name"):
            chunk.append("<|name|>" + str(msg["name"]))
        if msg.get("tool_call_id"):
            chunk.append("<|tool_call_id|>" + str(msg["tool_call_id"]))
        for tc in msg.get("tool_calls") or []:
            fn = (tc or {}).get("function", {})
            chunk.append("<|tool_call|>" + str(fn.get("name", "")) + str(fn.get("arguments", "")))
        parts.append("".join(chunk))
    return "\n".join(parts)


def common_prefix_len(a: str, b: str) -> int:
    """Length in characters of the shared leading run of two strings."""
    lo, hi = 0, min(len(a), len(b))
    while lo < hi:  # binary search: these prompts are big and mostly identical
        mid = (lo + hi + 1) // 2
        if a[:mid] == b[:mid]:
            lo = mid
        else:
            hi = mid - 1
    return lo


def reconcile(local: dict, provider: dict) -> dict:
    """Local counts vs. the provider's `usage` block, per call.

    Aggregated by `report_stats --reconcile`. A stable non-zero prompt delta is
    normally the tool-schema approximation above, or a provider-side template we
    cannot see; a delta that grows with history length means the tokenizer
    itself is wrong for the model.
    """
    out: dict = {}
    for field in ("prompt", "completion"):
        lv, pv = local.get(field), provider.get(field)
        if isinstance(lv, int) and isinstance(pv, int):
            out[field + "_delta"] = lv - pv
            out[field + "_error_pct"] = round(100.0 * (lv - pv) / pv, 3) if pv else None
    return out
