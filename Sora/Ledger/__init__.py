'''
Ledger
======

Accounting first, features second: if we bolt cost tracking on at the end we
will have already spent the $20 cap without knowing where it went.

Every LLM call this repo makes is metered here. There is exactly ONE recording
hook - `meter.metered()` - and exactly ONE output - `out/trace.jsonl`.
`llm/client.py:chat()` delegates to the hook, so no caller has to remember to
opt in, and any framework that manages to make a call without appearing in the
trace has bypassed `llm.client`, which is itself a finding worth writing down
(ASSIGNMENT.md 8.1).

What each trace line carries (ASSIGNMENT.md 8.1 + 5.1 + 5.4 + 2):

  request.messages        the exact messages sent, verbatim
  request.tools           the tool schemas exposed on that call
  tokens.components       token count per context component
                          (system / persona / memory / history / tool_output /
                          tool_schemas / overhead), summing to the local total
  tokens.provider|local   provider `usage` next to our own count, plus
  tokens.reconciliation   the delta between them - we count with tiktoken
                          o200k_base and never pretend that is authoritative
  tokens.headroom         room left under the 8k ceiling (SORA_CONTEXT_CEILING)
  sampling                model + every sampling parameter sent, and which
                          defaults we left unset
  latency.total_ms        wall clock around the call
  response.tool_calls     tool calls the model asked for
  cost                    dollars, provider-reported where available, local
                          price table as a cross-check
  cache                   provider cached-token hit rate AND the byte-identical
                          prompt-prefix fraction vs. the previous call in the
                          same lane (5.4 accepts the latter when the provider
                          does not expose the former)

Tool results get their own `tool_call` records in the same file via
`tool_span()`, with latency, result size and errors.

Categories (chat / compaction / memory / guardrails / tools / judge / eval)
and experiment tags are set ambiently, so they survive being called from inside
somebody else's loop:

    from Sora import Ledger

    with Ledger.call_context(category="chat", session_id="session_1", turn=3):
        reply = llm.client.chat(messages, tools=TOOLS)     # metered, attributed

Tagging is what makes the cache-layout A/B in ASSIGNMENT.md 5.4 measurable:
run the "before" layout with SORA_LEDGER_TAG=before, reorder the context, run
again with SORA_LEDGER_TAG=after, then

    python -m Sora.Ledger.report_stats --compare-cache before after

Reporting:

    python -m Sora.Ledger.report_stats                  # totals + per category
    python -m Sora.Ledger.report_stats --by tag|session|model|category|lane
    python -m Sora.Ledger.report_stats --last 1         # newest entry, verbatim
    python -m Sora.Ledger.report_stats --reconcile      # tokenizer drift
    python -m Sora.Ledger.report_stats --compare-cache before after

We keep every message body for now because debugging an agent from a redacted
trace is guesswork. Production needs a redaction pass at `trace.write()` (one
choke point, deliberately) plus a retention policy; both are out of scope here
and called out in NOTES.md rather than half-built.
'''
from .config import context_ceiling, run_id, session_id, tag, trace_path
from .meter import (
    CATEGORIES,
    call_context,
    current_context,
    metered,
    summary_line,
    tool_span,
    totals,
)

__all__ = [
    "CATEGORIES",
    "call_context",
    "chat",
    "context_ceiling",
    "current_context",
    "metered",
    "print_summary",
    "run_id",
    "session_id",
    "summary_line",
    "tag",
    "tool_span",
    "totals",
    "trace_path",
]


def chat(messages, tools=None, *, category=None, tags=None, tag=None,
         session_id=None, turn=None, cache_lane=None, note=None, **kwargs):
    """Metered chat completion - the explicit form of `call_context` + call.

    Identical in effect to calling `llm.client.chat` inside a `call_context`;
    both land on the same hook, so use whichever reads better at the call site.
    """
    from llm.client import chat as _client_chat

    with call_context(category=category, tags=tags, tag=tag, session_id=session_id,
                      turn=turn, cache_lane=cache_lane, note=note):
        return _client_chat(messages, tools=tools, **kwargs)


def print_summary(prefix: str = "") -> None:
    """One line of running totals for this process. Cheap enough to call at the
    end of every session; the authoritative numbers come from report_stats."""
    print(prefix + summary_line())
