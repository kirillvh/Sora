# Streaming and a violation at token 300

*ASSIGNMENT.md 6.4 — design answer, ~150 words. Not implemented; the agent
replies in one blocking call today.*

**Position: stream behind a one-sentence buffer, check per sentence, never
retract.**

Full buffering is the safe answer and the wrong one. It converts time-to-first-token
into time-to-whole-reply — roughly 1.4s to 4s at our measured
latencies — and a companion that pauses four seconds before every line stops
feeling alive. That cost is paid on 100% of turns to catch the ~1% that fail.

Rollback is cheapest in latency and worst in experience. Watching Sora say
something and then watching it get erased tells the user a censor is standing
behind her; it breaks the fiction harder than any refusal.

So: hold one sentence back, run the post-check on each completed sentence, and
release the previous one when it clears. Perceived cost is one sentence of lag
(~300ms), not a whole reply. A violation at token 300 stops the stream where it
is and lets her trail off into a dead-end — she reads as *deciding* to stop,
which is in character, rather than as being cut off.

The residual risk is the first sentence being fine and the fifth not, leaving
four visible sentences of context around an abandoned thought. Acceptable:
partial output plus a visible stop is a person changing her mind. Erasure is a
system failure.

Cost: one classifier call per sentence group instead of per turn — roughly 3–5x
the post-check spend. Batch every two sentences to halve it.
