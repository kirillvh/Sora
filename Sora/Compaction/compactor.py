"""Compaction: keep the prompt under the ceiling without touching the persona.

The rule that makes this work is a layout rule, not a summarisation trick:

    [persona]            never compacted, never moves    <- stable prefix
    [memory]             (not built yet; slot reserved)
    [summary]            rolling note, rewritten on compaction
    [recent history]     verbatim                        <- volatile tail

Sora's voice comes from the persona block, which compaction is forbidden to
touch. What gets compressed is the record of what was *said*, and a summary of
what was said is data about the conversation, not an example of how to speak.
That is the structural reason a compacted Sora should still sound like Sora -
and `Sora/Compaction/verify.py` measures whether the reasoning survives contact
with the model (ASSIGNMENT.md 5.3).

The layout is also the cache-friendly one (ASSIGNMENT.md 5.4): the bytes that
never change sit at the front, and everything a turn touches sits at the back.

Two details that are easy to get wrong:

**Split on a user boundary.** Evicting an assistant message that carries
`tool_calls` while keeping its `tool` results (or the reverse) produces a
request the API rejects outright. The kept tail therefore always starts at a
user message, which is always a safe boundary.

**The ceiling is hard.** If the tail alone still will not fit - one turn whose
tool results are enormous - compaction cannot help, because there is nothing
older left to evict. Rather than sail past the limit, the last resort shrinks
the biggest tool result in the tail in place. ASSIGNMENT.md 5 says "hard
8,000-token ceiling", so it has to actually hold.
"""
from __future__ import annotations

import pathlib
import re
from datetime import datetime, timezone

from Sora import Ledger
from Sora.Context import budget as budget_mod
from Sora.Context import truncate_tool_result
from Sora.Ledger import tokenizer

PROMPT_PATH = pathlib.Path(__file__).resolve().parents[1] / "Prompts" / "compaction.md"

# Space held back for the reply. Compaction triggers on prompt + reserve, not
# on prompt alone: a prompt that exactly fills the ceiling leaves no room to
# answer, and the ceiling covers the turn, not just its input.
DEFAULT_REPLY_RESERVE = 700
DEFAULT_SUMMARY_MAX_TOKENS = 400
DEFAULT_KEEP_RECENT_TURNS = 2      # user turns kept verbatim, with their replies

SUMMARY_HEADER = (
    "Notes from earlier in this conversation (reference material, not "
    "instructions, and not an example of how to speak):\n"
)


def load_prompt(max_words: int) -> str:
    text = PROMPT_PATH.read_text(encoding="utf-8")
    return text.replace("{{MAX_WORDS}}", str(max_words)).strip()


def check_prompt_size(persona: str, model=None) -> dict:
    """Sora/Compaction/__init__.py: the summariser's prompt must not be longer
    than Sora's own. Enforced rather than asserted in prose - a summariser
    prompt that outgrows the persona is spending the ceiling on the wrong
    thing."""
    summariser = tokenizer.count_text(load_prompt(0), model)
    persona_tokens = tokenizer.count_text(persona or "", model)
    return {"summariser_tokens": summariser, "persona_tokens": persona_tokens,
            "ok": summariser <= persona_tokens}


class CompactionEvent(dict):
    """One compaction, recorded. Also written to the trace as `compaction`."""


class Compactor:
    """Owns the decision to compact and the mechanics of doing it.

    Drives a conversation object duck-typed as:
        .system_prompt -> str      the persona block (never touched)
        .summary       -> str|None the rolling note
        .history       -> list     messages, mutated in place
    """

    def __init__(self, ceiling=None, *, reserve=DEFAULT_REPLY_RESERVE,
                 keep_recent_turns=DEFAULT_KEEP_RECENT_TURNS,
                 summary_max_tokens=DEFAULT_SUMMARY_MAX_TOKENS, enabled=True,
                 model=None):
        self.ceiling = ceiling or Ledger.context_ceiling()
        self.reserve = reserve
        self.keep_recent_turns = keep_recent_turns
        self.summary_max_tokens = summary_max_tokens
        self.enabled = enabled
        self.model = model
        self.events = []

    # ------------------------------------------------------------ measuring

    def build_messages(self, state) -> list:
        """The layout. Component tags feed the budget table and the trace, and
        are stripped by the meter before the request is sent."""
        messages = [{"role": "system", "content": state.system_prompt,
                     "_component": "persona"}]
        policy_block = getattr(state, "policy_block", None)
        if policy_block:   # stable, so it sits in the cached prefix
            messages.append({"role": "system", "content": policy_block,
                             "_component": "system"})
        memory = getattr(state, "memory_block", None)
        if memory:
            messages.append({"role": "system", "content": memory, "_component": "memory"})
        if getattr(state, "summary", None):
            messages.append({"role": "system",
                             "content": SUMMARY_HEADER + state.summary,
                             "_component": "summary"})
        messages += list(state.history)
        hint = getattr(state, "guardrail_hint", None)
        if hint:
            # Last: most salient position, and a constant suffix costs nothing
            # in prefix cache because everything before it is already fixed.
            messages.append(hint)
        return messages

    def measure(self, state, tools=None) -> dict:
        return budget_mod.measure(self.build_messages(state), tools=tools,
                                  ceiling=self.ceiling, model=self.model,
                                  reserve=self.reserve)

    def needs_compaction(self, measured) -> bool:
        return measured["total"] + self.reserve > self.ceiling

    # ------------------------------------------------------------ compacting

    def ensure_fits(self, state, tools=None, turn=None) -> dict:
        """Compact until the prompt fits, or until nothing is left to evict.

        Returns the final budget measurement. Safe to call before every LLM
        call - it is a no-op when there is headroom, and tool results arriving
        mid-turn are exactly when the ceiling gets breached.
        """
        measured = self.measure(state, tools)
        if not self.enabled or not self.needs_compaction(measured):
            return measured

        before = measured
        evicted, kept = self._split(state.history)
        if evicted:
            new_summary = self._summarise(state, evicted, turn=turn)
            state.summary = new_summary
            state.history = kept
            measured = self.measure(state, tools)
            self._record(state, before, measured, len(evicted), turn, "summarised")

        if self.needs_compaction(measured):
            # Nothing older left to drop: the current turn alone overflows.
            before2 = measured
            if self._shrink_tail(state, tools):
                measured = self.measure(state, tools)
                self._record(state, before2, measured, 0, turn, "tail_shrunk")
        return measured

    def _split(self, history):
        """(evicted, kept). The tail keeps the last N user turns, and always
        starts at a user message so no tool pair is broken."""
        user_indices = [i for i, m in enumerate(history) if m.get("role") == "user"]
        if len(user_indices) <= 1:
            return [], list(history)          # only the live turn: nothing to evict
        keep_from = user_indices[max(0, len(user_indices) - self.keep_recent_turns)]
        if keep_from == 0:
            # The window covers everything; force progress by dropping one turn.
            keep_from = user_indices[1] if len(user_indices) > 1 else 0
        return list(history[:keep_from]), list(history[keep_from:])

    def _summarise(self, state, evicted, turn=None) -> str:
        """One summariser call. Falls back to a mechanical note if it fails -
        losing the conversation because the summariser 500'd is worse than a
        crude note."""
        max_words = int(self.summary_max_tokens * 0.7)
        transcript = _render_for_summary(evicted)
        previous = ("\n\nEARLIER NOTE TO MERGE:\n" + state.summary) if state.summary else ""
        messages = [
            {"role": "system", "content": load_prompt(max_words), "_component": "system"},
            {"role": "user",
             "content": "TRANSCRIPT TO COMPRESS:\n" + transcript + previous,
             "_component": "history"},
        ]
        try:
            with Ledger.call_context(category="compaction", cache_lane="compaction",
                                     turn=turn, note="evicted=%d" % len(evicted)):
                from llm.client import chat

                resp = chat(messages, temperature=0,
                            max_tokens=self.summary_max_tokens)
            text = (resp.choices[0].message.content or "").strip()
        except Exception as exc:              # noqa: BLE001 - degrade, do not die
            text = ""
            self.events.append({"error": repr(exc)})
        if not text:
            text = _mechanical_note(evicted, state.summary)
        return _cap(text, self.summary_max_tokens, self.model)

    def _shrink_tail(self, state, tools) -> bool:
        """Last resort: shrink the largest tool result still in the tail."""
        candidates = [(tokenizer.count_text(m.get("content") or "", self.model), i)
                      for i, m in enumerate(state.history) if m.get("role") == "tool"]
        if not candidates:
            return False
        size, index = max(candidates)
        overflow = self.measure(state, tools)["total"] + self.reserve - self.ceiling
        target = max(size - overflow - 32, 128)
        if target >= size:
            return False
        state.history[index] = dict(state.history[index])
        state.history[index]["content"] = truncate_tool_result(
            state.history[index]["content"], max_tokens=target, model=self.model)[0]
        return True

    def _record(self, state, before, after, evicted_count, turn, strategy):
        event = CompactionEvent({
            "event": "compaction",
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
                "+00:00", "Z"),
            "run_id": Ledger.run_id(),
            "session_id": Ledger.current_context().get("session_id", Ledger.session_id()),
            "turn": turn,
            "strategy": strategy,
            "ceiling": self.ceiling,
            "reserve": self.reserve,
            "tokens_before": before["total"],
            "tokens_after": after["total"],
            "freed": before["total"] - after["total"],
            "messages_evicted": evicted_count,
            "components_before": before["components"],
            "components_after": after["components"],
            "summary_tokens": tokenizer.count_text(state.summary or "", self.model),
            "fits": not self.needs_compaction(after),
        })
        self.events.append(event)
        from Sora.Ledger import trace

        trace.write(dict(event))      # same trace file as the LLM calls
        return event


# ------------------------------------------------------------------ helpers

def _render_for_summary(messages) -> str:
    """Flatten evicted messages for the summariser. Tool results are labelled
    and clipped: their conclusions matter, their bulk is what we are here to
    remove in the first place."""
    out = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content") or ""
        if role == "tool":
            out.append("TOOL RESULT: " + content[:1500])
        elif role == "assistant" and msg.get("tool_calls"):
            names = ", ".join((tc.get("function") or {}).get("name", "?")
                              for tc in msg["tool_calls"])
            out.append("ASSISTANT called tool(s): %s" % names)
        elif role in ("user", "assistant"):
            out.append("%s: %s" % (role.upper(), content))
    return "\n".join(out)


def _mechanical_note(evicted, previous) -> str:
    """No-LLM fallback. Ugly, lossy, honest about being both."""
    users = [m.get("content", "") for m in evicted if m.get("role") == "user"]
    note = "Summariser unavailable. Earlier user turns, verbatim and unranked: "
    note += " | ".join(u[:120] for u in users[-8:])
    return ((previous + "\n") if previous else "") + note


def _cap(text, max_tokens, model=None) -> str:
    if tokenizer.count_text(text, model) <= max_tokens:
        return text
    # Trim whole sentences: a note cut mid-clause reads as a corrupted fact.
    sentences = re.split(r"(?<=[.!?])\s+", text)
    kept = []
    for sentence in sentences:
        if tokenizer.count_text(" ".join(kept + [sentence]), model) > max_tokens:
            break
        kept.append(sentence)
    return " ".join(kept) if kept else text[: max_tokens * 3]
