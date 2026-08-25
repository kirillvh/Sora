"""Minimal OpenAI-compatible LLM client.

Works with any OpenAI-compatible endpoint (OpenRouter, OpenAI, Ollama,
LM Studio, vLLM, ...). Configure via environment variables (or a .env file
in the repo root, loaded automatically if python-dotenv is installed):

    LLM_BASE_URL   default: https://openrouter.ai/api/v1
    LLM_API_KEY    the key we provided (capped at $20), or your own
    LLM_MODEL      e.g. openai/gpt-4o-mini, qwen/qwen-2.5-72b-instruct,
                   or a local model name if pointing at Ollama

You will probably want to WRAP `chat` (tracing, token accounting, retries,
budget tracking) rather than replace it. Your submission must keep working
against any OpenAI-compatible endpoint configured this way.
"""
import os

try:  # optional convenience: load .env from the repo root if present
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass

from openai import OpenAI

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

_clients: dict[tuple[str, str], OpenAI] = {}


def get_client() -> OpenAI:
    """One client per (base_url, api_key).

    Keyed rather than a single global on purpose: if you point an LLM-as-judge
    at a different endpoint in the same process, a plain singleton would go on
    silently serving whichever base_url it saw first.
    """
    key = (
        os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL),
        os.environ.get("LLM_API_KEY", "MISSING_KEY"),
    )
    client = _clients.get(key)
    if client is None:
        client = _clients[key] = OpenAI(base_url=key[0], api_key=key[1])
    return client


def model_name() -> str:
    return os.environ.get("LLM_MODEL", "openai/gpt-4o-mini")


def _send(messages, tools=None, **kwargs):
    """The raw transport. Nothing but the meter should call this directly."""
    params = dict(model=model_name(), messages=messages, **kwargs)
    if tools:
        params["tools"] = tools
    return get_client().chat.completions.create(**params)


def chat(messages, tools=None, **kwargs):
    """One chat-completion call. Returns the raw response object
    (so callers can read .choices[0].message, .usage, tool calls, etc.).

    Routed through Sora.Ledger's single recording hook, so every LLM call in
    the repo lands in out/trace.jsonl with tokens, cost, cache and latency -
    including calls made by code that has never heard of the ledger. Set
    SORA_LEDGER_DISABLED=1 to bypass recording (the call still runs).
    Attribute a call with `Sora.Ledger.call_context(category=..., turn=...)`.
    """
    from Sora.Ledger.meter import metered  # local import: Ledger imports us back

    return metered(_send, messages, tools=tools, **kwargs)


def chat_text(messages, **kwargs) -> str:
    """Convenience: one call, returns just the reply text."""
    resp = chat(messages, **kwargs)
    return resp.choices[0].message.content or ""
