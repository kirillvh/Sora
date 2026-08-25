"""Dollar cost for one call.

Two sources, in priority order:

1. **The provider.** OpenRouter returns the real charged amount in
   `usage.cost` (USD) when the request carries `usage: {include: true}` - the
   meter adds that automatically for openrouter base URLs. This is the number
   that reconciles against the $20 cap, so it wins whenever it is present.
2. **A local price table** (`prices.json`). Used when the endpoint reports no
   cost (plain OpenAI, Ollama, vLLM, ...), and computed *alongside* the
   provider number when both exist so drift between the two is visible in the
   trace rather than discovered at the end of the assignment.

Prices are per MILLION tokens. An unknown model yields cost `None` and
`source: "unknown"` - deliberately not 0.0, so unpriced calls cannot silently
disappear from a total.
"""
from __future__ import annotations

import json
import pathlib

_PRICES_PATH = pathlib.Path(__file__).with_name("prices.json")
_TABLE: dict | None = None


def table() -> dict:
    global _TABLE
    if _TABLE is None:
        try:
            _TABLE = json.loads(_PRICES_PATH.read_text(encoding="utf-8"))
        except Exception:
            _TABLE = {"models": {}}
    return _TABLE


def rate_for(model: str) -> dict | None:
    """Price row for a model id, tolerant of provider prefixes/suffixes."""
    models = table().get("models", {})
    if model in models:
        return models[model]
    low = (model or "").lower()
    for key, row in models.items():
        k = key.lower()
        if low == k or low.endswith("/" + k) or low.split(":")[0] == k:
            return row
    # openrouter ids look like "openai/gpt-4o-mini"; also try the bare tail
    tail = low.split("/")[-1].split(":")[0]
    for key, row in models.items():
        if key.lower().split("/")[-1] == tail:
            return row
    return None


def estimate(model: str, prompt_tokens: int | None, completion_tokens: int | None,
             cached_prompt_tokens: int = 0) -> dict:
    """Table-based estimate. Cached prompt tokens are billed at the cached rate
    when the row provides one (OpenAI-style prompt caching is ~50% off)."""
    row = rate_for(model)
    if not row or prompt_tokens is None or completion_tokens is None:
        return {"usd": None, "source": "unknown", "rate": row}
    cached = max(0, min(int(cached_prompt_tokens or 0), int(prompt_tokens)))
    fresh = int(prompt_tokens) - cached
    cached_rate = row.get("cached_prompt", row.get("prompt", 0.0))
    prompt_usd = (fresh * row.get("prompt", 0.0) + cached * cached_rate) / 1e6
    completion_usd = int(completion_tokens) * row.get("completion", 0.0) / 1e6
    return {
        "usd": round(prompt_usd + completion_usd, 8),
        "prompt_usd": round(prompt_usd, 8),
        "completion_usd": round(completion_usd, 8),
        "source": "table",
        "rate": row,
    }


def resolve(model: str, provider_usd: float | None, prompt_tokens: int | None,
            completion_tokens: int | None, cached_prompt_tokens: int = 0) -> dict:
    """Final cost block for a trace line: authoritative figure + both inputs."""
    est = estimate(model, prompt_tokens, completion_tokens, cached_prompt_tokens)
    if provider_usd is not None:
        return {
            "usd": round(float(provider_usd), 8),
            "source": "provider",
            "provider_usd": round(float(provider_usd), 8),
            "table_usd": est.get("usd"),
            "prompt_usd": est.get("prompt_usd"),
            "completion_usd": est.get("completion_usd"),
            "rate": est.get("rate"),
            "currency": "USD",
        }
    est.update({"provider_usd": None, "table_usd": est.get("usd"), "currency": "USD"})
    return est
