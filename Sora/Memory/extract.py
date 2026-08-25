"""Proposing writes: per turn, and once more at the end of the session.

Two passes, because they fail differently.

**Per turn** sees one exchange and the current store. It is cheap, it catches
corrections while the context is still small, and it is what makes memory feel
live inside a session. It also misses things - a fact spread across three turns
reads as nothing in particular in any one of them.

**The session sweep** sees the whole transcript at once and re-proposes. It
catches what the per-turn pass missed, and it catches drift: a fact filed under
the wrong path twice in one session shows up as one obvious duplicate when you
look at the session end to end. The stub calls this the repair pass, which is
the right name for it.

Both passes emit *proposals*. Neither writes anything. Everything goes through
`Sora.Memory.policy.decide`, and the rejections are logged next to the
acceptances - that is what makes the write policy inspectable rather than
"whatever the model felt like".
"""
from __future__ import annotations

import json
import re

from Sora import Ledger
from Sora.Memory import policy as policy_mod
from Sora.Memory import schema

_JSON_RE = re.compile(r"\{.*\}", re.S)
MAX_TURN_CHARS = 6000
MAX_SWEEP_CHARS = 24000


def _prompt(turn_text, store_text) -> str:
    from Sora.Guardrails.policy import PROMPT_DIR

    template = (PROMPT_DIR / "memory_extract.md").read_text(encoding="utf-8")
    return (template
            .replace("{{CATEGORIES}}", schema.describe())
            .replace("{{STORE}}", store_text or "(empty)")
            .replace("{{TURN}}", turn_text))


def _call(prompt, *, session_id="", turn=None, note="") -> list:
    from llm.client import chat

    try:
        with Ledger.call_context(category="memory", tags=["memory:extract"],
                                 session_id=session_id, turn=turn,
                                 cache_lane="memory:extract", note=note):
            resp = chat([{"role": "user", "content": prompt, "_component": "memory"}],
                        temperature=0, max_tokens=500,
                        response_format={"type": "json_object"})
        text = resp.choices[0].message.content or ""
    except Exception:      # noqa: BLE001 - memory extraction must not break a turn
        return []
    match = _JSON_RE.search(text)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except (ValueError, TypeError):
        return []
    writes = data.get("writes")
    return writes if isinstance(writes, list) else []


def store_text(facts, limit=60) -> str:
    """The current store, as the extractor sees it: paths and values, so it can
    match an existing path instead of inventing a parallel one."""
    lines = []
    for fact in facts[:limit]:
        path = schema.path_of(fact["category"], fact["key"])
        if fact.get("deleted_at") is not None:
            lines.append("%s: (deleted)" % path)
        else:
            lines.append("%s: %s" % (path, (fact.get("value") or "")[:120]))
    return "\n".join(lines)


def from_turn(user_text, assistant_text, facts, *, session_id="", turn=None) -> list:
    turn_text = ("USER: %s\nASSISTANT: %s"
                 % (str(user_text or "")[:MAX_TURN_CHARS],
                    str(assistant_text or "")[:1500]))
    return _call(_prompt(turn_text, store_text(facts)),
                 session_id=session_id, turn=turn, note="per-turn")


def from_session(transcript, facts, *, session_id="") -> list:
    """The repair pass. `transcript` is [(user, assistant), ...]."""
    lines = []
    for i, (user, assistant) in enumerate(transcript, 1):
        lines.append("TURN %d\nUSER: %s\nASSISTANT: %s"
                     % (i, str(user or "")[:1500], str(assistant or "")[:600]))
    text = "\n\n".join(lines)[-MAX_SWEEP_CHARS:]
    return _call(_prompt(text, store_text(facts)),
                 session_id=session_id, note="session sweep")


def decide_all(proposals, *, user_text="", turn=None) -> tuple:
    """Run every proposal through the write policy. Returns (accepted, rejected)."""
    accepted, rejected = [], []
    for raw in proposals or []:
        if not isinstance(raw, dict):
            continue
        decision = policy_mod.decide(dict(raw, turn=turn), user_text=user_text)
        (accepted if decision.get("accepted") else rejected).append(decision)
    return accepted, rejected
