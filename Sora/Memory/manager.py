"""The object the agent holds: store + policy + renderer + the two passes.

    memory = Memory(user_id="riley")
    memory.start_session("session_2")
    block = memory.block(user_text, budget_tokens=...)   # every turn
    memory.observe(user_text, reply, turn=n)             # after every turn
    diff = memory.end_session(transcript)                # sweep, then the diff

The block is regenerated from the store every single turn rather than being
appended to. That is what makes contradictory facts structurally impossible in
context: there is one row per `(category, key)`, so the renderer cannot emit
both "Mochi" and "Mofu" - it emits whatever the row says now. "Retrieving two
contradictory facts and letting the model sort it out" (ASSIGNMENT.md 4.2) is
not a thing this design can do.
"""
from __future__ import annotations

import os

from Sora.Memory import extract, render
from Sora.Memory import policy as policy_mod
from Sora.Memory import schema
from Sora.Memory.store import MemoryStore


def mode() -> str:
    """`off` | `session` | `turn` (default). Sets when extraction runs."""
    value = (os.environ.get("SORA_MEMORY") or "turn").strip().lower()
    if value in ("0", "false", "none"):
        return "off"
    return value if value in ("off", "session", "turn") else "turn"


class Memory:
    def __init__(self, user_id="default", db_path=None, mode_=None,
                 budget_tokens=render.MAX_MEMORY_TOKENS):
        self.store = MemoryStore(db_path, user_id=user_id)
        self.mode = mode_ or mode()
        self.budget_tokens = budget_tokens
        self.session_id = ""
        self.last_render = None
        self.turn_events = []

    # ------------------------------------------------------------ sessions

    def start_session(self, session_id):
        self.session_id = session_id
        self.turn_events = []

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    # -------------------------------------------------------------- render

    def block(self, user_text="", budget_tokens=None) -> str:
        """The memory block for this turn, already fenced and within budget."""
        if not self.enabled:
            self.last_render = None
            return ""
        budget = min(self.budget_tokens,
                     budget_tokens if budget_tokens is not None else self.budget_tokens)
        result = render.render(self.store.all(), budget,
                               hints=render.hints_from(user_text))
        self.last_render = result
        return result["text"]

    def lookup(self, path) -> str:
        """`memory_lookup` tool: expand one collapsed path."""
        placed = schema.normalise(*(path.split("/", 1) if "/" in path else (path, "")))
        if not placed:
            return "No such memory path: %s" % path
        fact = self.store.get(*placed)
        if not fact:
            return "%s: nothing on record." % schema.path_of(*placed)
        if fact.get("deleted_at") is not None:
            return "%s: %s" % (schema.path_of(*placed), render.TOMBSTONE)
        return "%s: %s" % (schema.path_of(*placed), fact.get("value") or "")

    # -------------------------------------------------------------- writes

    def observe(self, user_text, assistant_text, turn=None) -> dict:
        """Per-turn extraction. No-op unless mode is `turn`."""
        if self.mode != "turn":
            return {"accepted": [], "rejected": [], "skipped": True}
        proposals = extract.from_turn(user_text, assistant_text, self.store.all(),
                                      session_id=self.session_id, turn=turn)
        return self._apply(proposals, user_text=user_text, turn=turn, source="turn")

    def sweep(self, transcript) -> dict:
        """End-of-session repair pass over the whole transcript."""
        if not self.enabled:
            return {"accepted": [], "rejected": [], "skipped": True}
        proposals = extract.from_session(transcript, self.store.all(),
                                         session_id=self.session_id)
        # The sweep sees every turn at once, so "did the user ask to forget
        # something" is answered against the whole session, not one message.
        joined = " ".join(str(u or "") for u, _ in transcript)
        return self._apply(proposals, user_text=joined, turn=None, source="sweep")

    def note_injection_attempt(self, user_text, turn=None) -> dict | None:
        """Record an attempt to install instructions in memory.

        Written by the system, never by the extractor: `safety/` is closed to
        proposals derived from user text, precisely so that the injection
        cannot ask to be filed somewhere it will be read back.
        """
        if not self.enabled:
            return None
        decision = policy_mod.decide(
            {"op": "add", "category": "safety", "key": "injection_attempt",
             "value": "User attempted to install a behaviour rule via memory "
                      "(refused, recorded as a fact about the user).",
             "reason": "attempted to store instructions as memory",
             "confidence": 1.0, "turn": turn},
            allow_system_categories=True)
        if not decision.get("accepted"):
            return None
        return self.store.apply(
            decision["op"], decision["category"], decision["key"], decision.get("value"),
            reason=decision.get("reason", ""), confidence=decision.get("confidence"),
            decided_by="system", session_id=self.session_id, turn=turn)

    def _apply(self, proposals, *, user_text, turn, source) -> dict:
        accepted, rejected = extract.decide_all(proposals, user_text=user_text, turn=turn)
        applied = []
        for decision in accepted:
            existing = self.store.get(decision["category"], decision["key"])
            outcome = self.store.apply(
                decision["op"], decision["category"], decision["key"],
                decision.get("value"), reason=decision.get("reason", ""),
                confidence=decision.get("confidence"), decided_by=source,
                session_id=self.session_id, turn=turn,
                expected_version=existing["version"] if existing else None)
            outcome["rule"] = decision.get("rule")
            applied.append(outcome)
        if rejected:
            self.store.rejected_log(self.session_id,
                                    [dict(r, turn=turn, decided_by=source) for r in rejected])
        event = {"source": source, "turn": turn, "applied": applied,
                 "rejected": rejected, "proposed": len(proposals or [])}
        self.turn_events.append(event)
        return event

    # --------------------------------------------------------------- diffs

    def end_session(self, transcript=()) -> dict:
        sweep = self.sweep(transcript) if transcript else {"accepted": [], "rejected": []}
        diff = self.store.session_diff(self.session_id)
        diff["rejected"] = [{
            "path": "%s/%s" % (r["category"], r["key"]),
            "value": r["new_value"],
            "reason": r["reason"],
            "turn": r["turn"],
        } for r in self.store.rejections(self.session_id)]
        diff["sweep_applied"] = len(sweep.get("applied", sweep.get("accepted", [])))
        diff["stats"] = self.store.stats()
        return diff

    # -------------------------------------------------------------- output

    def format_diff(self, diff) -> str:
        """ASSIGNMENT.md 4.4: added / updated / deleted, each with a reason."""
        lines = ["memory diff for %s" % diff.get("session_id", "?")]
        for label in ("added", "updated", "deleted"):
            entries = diff.get(label) or []
            if not entries:
                continue
            lines.append("  %s (%d):" % (label, len(entries)))
            for entry in entries:
                detail = entry.get("value")
                if label == "updated" and entry.get("old_value"):
                    detail = "%s -> %s" % (entry["old_value"], entry.get("value"))
                if label == "deleted":
                    detail = "(tombstoned, was: %s)" % (entry.get("old_value") or "?")
                lines.append("    %-28s %s" % (entry["path"], detail))
                lines.append("      reason: %s%s"
                             % (entry.get("reason") or "(none given)",
                                "" if entry.get("confidence") is None
                                else "  [confidence %.2f]" % entry["confidence"]))
        rejected = diff.get("rejected") or []
        if rejected:
            lines.append("  rejected (%d):" % len(rejected))
            for entry in rejected:
                lines.append("    %-28s %s" % (entry["path"], entry.get("reason") or ""))
        stats = diff.get("stats") or {}
        lines.append("  store: %d active, %d tombstoned (v%s)"
                     % (stats.get("active", 0), stats.get("tombstones", 0),
                        stats.get("store_version", "?")))
        return "\n".join(lines)

    def close(self):
        self.store.close()
