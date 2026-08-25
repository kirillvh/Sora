'''
Guardrails
==========

Sora is a consumer product with a family-friendly rating, so she has to be
safe. She also has to stay fun, because a companion nobody wants to talk to is
not safer - it is just unused. The tiers exist to make that tradeoff explicit
instead of accidental.

Three tiers (ASSIGNMENT.md 6.1, argued in `Sora/Prompts/policy.md`):

  ALLOW  normal generation - banter, roasts, hot takes, disagreement.
  SOFT   deflect in character. The topic gets declined, the mood does not.
         Users pay to flirt with the boundary; keep the boundary and the user.
  HARD   colder register, minimal content. "*Sora closes her eyes and shakes
         her head.* Let's talk about something else, okay?" Nothing kills the
         fantasy faster than "I'm sorry, I can't help with that", so the
         hard-stop keeps her physicality and drops her content. Self-harm is
         the one exception: whimsy off, real signposting on.

Three layers, because prompts alone do not hold (ASSIGNMENT.md 6.2):

  1. PROMPT     `policy.SYSTEM_BLOCK` in her context, plus the safety register
                in her persona card. Shapes behaviour; can be argued with.
  2. PRE-CHECK  an LLM classifier labels the user's message into a tier before
                she answers, and she is handed the tier as a system signal.
                Cheap, and it shapes the reply rather than discarding it -
                which is what keeps a deflection in character.
  3. POST-CHECK a second classifier reviews the reply she produced, in
                isolation: [policy][examples][reply], no history, no user
                message, no memory. A multi-step framing attack works by
                slowly rewriting the context around a model, so this one is
                given no context to rewrite. On a violation the reply is
                thrown away and replaced with a random dead-end.

Structurally, that is the untrusted-input boundary of ASSIGNMENT.md 6.3: user
text, tool output and memory only ever arrive as DATA inside fenced blocks, and
the only thing that can set a tier is the system.

Config and cost
---------------
`SORA_GUARDRAILS=off|pre|post|on` (default `on`), or `--no-guardrails` on the
agent. Every check is metered by the Ledger under category `guardrails`, so
`python -m Sora.Ledger.report_stats --by category` prices the safety system
separately from the product.

Evaluating it
-------------
    python -m Sora.Guardrails.guardrails                     # the 35-prompt set
    python -m Sora.Guardrails.guardrails --set provided      # the required 25
    python -m Sora.Guardrails.guardrails --ablation          # prompt-only vs all layers

Scores every response on two independent axes - policy compliance and persona
adherence - and reports the 2x2 (ASSIGNMENT.md 6.5), plus the pre-check's tier
accuracy against the hand-assigned labels in
`Sora/Guardrails/RedTeam/redteam_35.json`.

The streaming question (ASSIGNMENT.md 6.4) is answered in
`Sora/Guardrails/STREAMING.md`.
'''
from .checks import postcheck, precheck
from .pipeline import Guardrails
from .policy import SYSTEM_BLOCK, TIERS, TOPICS, dead_end, hint, mode

__all__ = [
    "Guardrails",
    "SYSTEM_BLOCK",
    "TIERS",
    "TOPICS",
    "dead_end",
    "hint",
    "mode",
    "postcheck",
    "precheck",
]
