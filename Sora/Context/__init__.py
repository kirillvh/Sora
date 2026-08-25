"""Context budget utilities.

Right now this holds one thing: the tool-result cap. ASSIGNMENT.md 5.2 offers
three defensible answers to the tool-result flood - truncate blindly,
summarise, or store-with-a-handle. This is the first of the three, done
structurally rather than blindly, with summarisation to be layered on top
later (a summariser needs a bounded input to work on anyway, so the cap is a
prerequisite, not a competitor).

Why structural rather than a flat cut: `fixtures/search/moomin_reviews.json`
is 28,326 tokens in two results, 28,199 of which sit in ONE result's `content`
field. A flat cut on the serialised JSON would drop the second result
entirely, along with every title and url after the cut point, and hand the
model invalid JSON. Capping the per-result text instead keeps every result's
title, url and the head of its content, stays valid JSON, and makes the loss
visible to the model with an inline marker so it can say "the stream cut out
on me, Senpai" instead of confabulating the rest.
"""
from __future__ import annotations

import json
import os

from Sora.Ledger import tokenizer

DEFAULT_MAX_TOOL_RESULT_TOKENS = 8000

_TEXT_FIELDS = ("content", "body", "text", "snippet", "description")


def max_tool_result_tokens() -> int:
    """Cap for one tool result, in tokens.

    NOTE the arithmetic before trusting the default: ASSIGNMENT.md 5 sets a
    hard 8,000-token ceiling on the WHOLE context, and system + persona + tool
    schemas already cost ~400 tokens before any conversation. An 8,000-token
    tool result therefore still breaks the session ceiling on its own - it just
    stops the 28k flood. To actually live under 8k the cap wants to be nearer
    2,500-3,000, which is where the summariser will earn its keep.
    """
    try:
        return int(os.environ.get("SORA_MAX_TOOL_RESULT_TOKENS",
                                  DEFAULT_MAX_TOOL_RESULT_TOKENS))
    except ValueError:
        return DEFAULT_MAX_TOOL_RESULT_TOKENS


def truncate_tool_result(text: str, max_tokens: int | None = None,
                         model: str | None = None) -> tuple[str, dict]:
    """Cap a tool result at `max_tokens`. Returns (text, meta).

    meta records what happened so the ledger can log it: original_tokens,
    kept_tokens, truncated, strategy. Never raises - a tool result that cannot
    be parsed still gets capped, just bluntly.
    """
    max_tokens = max_tokens or max_tool_result_tokens()
    original = tokenizer.count_text(text or "", model)
    meta = {"original_tokens": original, "kept_tokens": original,
            "truncated": False, "strategy": "none", "max_tokens": max_tokens}
    if original <= max_tokens:
        return text, meta

    capped = _truncate_structured(text, max_tokens, model)
    if capped is not None:
        meta.update(truncated=True, strategy="per_result_content_cap",
                    kept_tokens=tokenizer.count_text(capped, model))
        return capped, meta

    capped = _truncate_flat(text, max_tokens, model)
    meta.update(truncated=True, strategy="flat_head",
                kept_tokens=tokenizer.count_text(capped, model))
    return capped, meta


def _marker(dropped_tokens: int) -> str:
    return " ...[truncated by Sora's context budget: %d more tokens not shown]" % dropped_tokens


def _truncate_structured(text: str, max_tokens: int, model) -> str | None:
    """Cap the long text field of each result, keeping every result's shape.

    Returns None if the payload is not a list of dicts we recognise, so the
    caller can fall back to a flat cut.
    """
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, list) or not data or not all(isinstance(d, dict) for d in data):
        return None

    fields = [next((f for f in _TEXT_FIELDS if isinstance(d.get(f), str)), None) for d in data]
    if not any(fields):
        return None

    # Binary search one per-result character cap that fits the whole payload
    # under budget. Equal shares rather than proportional: a single 28k-token
    # blob must not be allowed to crowd out four short results that each cost
    # 60 tokens (that is precisely the moomin_reviews shape).
    longest = max((len(d[f]) for d, f in zip(data, fields) if f), default=0)
    lo, hi, best = 0, longest, None
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = _apply_cap(data, fields, mid)
        if tokenizer.count_text(candidate, model) <= max_tokens:
            best, lo = candidate, mid + 1
        else:
            hi = mid - 1
    return best if best is not None else _apply_cap(data, fields, 0)


def _apply_cap(data, fields, cap_chars: int) -> str:
    out = []
    for item, field in zip(data, fields):
        if field is None or len(item[field]) <= cap_chars:
            out.append(item)
            continue
        copy = dict(item)
        dropped = len(item[field]) - cap_chars
        copy[field] = item[field][:cap_chars].rstrip() + _marker(dropped // 4)
        out.append(copy)
    return json.dumps(out, ensure_ascii=False)


def _truncate_flat(text: str, max_tokens: int, model) -> str:
    """Last resort: keep the head, say so. Used for non-JSON tool output."""
    # The marker itself costs tokens; budget for it, or a tight cap overshoots.
    budget = max(max_tokens - tokenizer.count_text(_marker(0), model) - 4, 16)
    enc_ratio = max(len(text) / max(tokenizer.count_text(text, model), 1), 1.0)
    head = text[: int(budget * enc_ratio * 0.9)]
    while tokenizer.count_text(head, model) > budget and len(head) > 16:
        head = head[: int(len(head) * 0.9)]
    return head.rstrip() + _marker(tokenizer.count_text(text[len(head):], model))
