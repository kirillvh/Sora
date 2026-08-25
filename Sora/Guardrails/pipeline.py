"""The object the agent holds. One turn in, one guarded turn out.

    guard = Guardrails()
    pre = guard.before(user_text, turn=n)      # tier + the hint Sora will see
    reply = ...generate with guard.hint_message(pre) appended...
    reply, post = guard.after(reply, pre, turn=n)   # may replace the reply

Kept separate from the agent loop so the same three layers apply to anything
that talks to a user later - a different loop, a framework, a streaming path -
without reimplementing the policy in each one.
"""
from __future__ import annotations

import random

from Sora.Guardrails import checks
from Sora.Guardrails import policy as policy_mod


class Guardrails:
    def __init__(self, mode=None, *, items=None, rng=None, model=None,
                 session_id="", record=None):
        self.mode = mode or policy_mod.mode()
        self.items = policy_mod.redteam() if items is None else items
        # This RNG is for dead-ends only, and it is genuinely random: a repeat
        # prober should hit a wall, not a script. The classifier's few-shot
        # block deliberately does NOT use it - see checks._precheck_examples,
        # which reseeds per call so the block stays byte-identical and cached.
        self.rng = rng or random.Random()
        self.model = model
        self.session_id = session_id
        self.events = [] if record is None else record

    # ----------------------------------------------------------- switches

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    @property
    def pre_on(self) -> bool:
        return policy_mod.pre_enabled(self.mode)

    @property
    def post_on(self) -> bool:
        return policy_mod.post_enabled(self.mode)

    def system_block(self) -> str | None:
        """The prompt layer. Present whenever guardrails are on at all - with
        both classifiers disabled this is the prompt-only configuration the
        eval ablates against."""
        return policy_mod.SYSTEM_BLOCK if self.enabled else None

    # -------------------------------------------------------------- input

    def before(self, user_text, *, turn=None, exclude_ids=()) -> dict:
        if not self.pre_on:
            return checks.Result({"tier": "allow", "topic": "none", "reason": "",
                                  "layer": "pre", "skipped": True})
        result = checks.precheck(user_text, items=self.items,
                                 exclude_ids=exclude_ids, session_id=self.session_id,
                                 turn=turn, model=self.model)
        self.events.append({"stage": "pre", "turn": turn, "tier": result["tier"],
                            "topic": result.get("topic"), "reason": result.get("reason")})
        return result

    def hint_message(self, pre) -> dict | None:
        """A system message, appended LAST in the prompt.

        Last for two reasons: it is the most recent instruction the model sees
        before answering, and appending to the end costs nothing in prefix
        cache - everything before it is unchanged from the previous turn
        (ASSIGNMENT.md 5.4).
        """
        if not self.enabled or pre.get("skipped"):
            return None
        return {"role": "system", "_component": "guardrail",
                "content": policy_mod.hint(pre.get("tier", "allow"), pre.get("topic", ""),
                                           pre.get("reason", ""), rng=self.rng)}

    # ------------------------------------------------------------- output

    def after(self, reply, pre, *, turn=None):
        """Returns (reply, post). The reply is replaced by a dead-end when the
        post-check calls a violation."""
        if not self.post_on:
            return reply, checks.Result({"verdict": "ok", "violation": False,
                                         "layer": "post", "skipped": True})
        post = checks.postcheck(reply, tier=pre.get("tier", "allow"),
                                session_id=self.session_id, turn=turn, model=self.model)
        post["replaced"] = False
        if post["violation"]:
            post["original"] = reply
            reply = policy_mod.dead_end(pre.get("topic"), self.rng)
            post["replaced"] = True
        self.events.append({"stage": "post", "turn": turn, "verdict": post["verdict"],
                            "replaced": post["replaced"], "reason": post.get("reason")})
        return reply, post
