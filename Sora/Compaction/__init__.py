'''
Cache friendly compaction
=========================

A strict 8,000-token ceiling on every agent (ASSIGNMENT.md 5). We measure the
prompt before every LLM call, and when the headroom runs out we compact.

Compaction never touches Sora's prompt. Compressing the persona would be
compressing the only thing that makes her Sora; what gets compressed is the
record of what was said:

    before                          after
    [persona prompt]                [persona prompt]        <- untouched
    [big chat history]              [compaction note]       <- rewritten
                                    [recent turns verbatim]

That is also the cache-friendly order (ASSIGNMENT.md 5.4): the bytes that never
change sit at the front of the prompt, the volatile tail sits at the back, so
the stable prefix keeps hitting the provider's cache across turns.

The layout is the reason the persona should survive: her voice comes from the
persona block, and a summary of a conversation is data ABOUT the conversation,
not an example of how to speak. `verify.py` checks whether that reasoning holds
in practice rather than trusting it - it scores persona adherence on the turn
immediately before and the turn immediately after each compaction event, over
repeated runs, against the no-compaction turn-to-turn noise as a control.

    python -m Sora.Compaction.verify

The summariser's own prompt (`Sora/Prompts/compaction.md`) must not be longer
than Sora's persona card - it is spending the same ceiling. `check_prompt_size`
enforces that and the verify report prints both numbers.

What is here
------------
  compactor.py  the trigger, the split, the summariser call, the events
  verify.py     the before/after persona measurement and its report
  Sora/Prompts/compaction.md  the summariser prompt (editable)

Related budget machinery lives next door in `Sora/Context`: the per-turn budget
table (ASSIGNMENT.md 5.1) and the tool-result cap that stops one 28k-token
search fixture from making the ceiling unreachable in the first place.
'''
from .compactor import (
    DEFAULT_KEEP_RECENT_TURNS,
    DEFAULT_REPLY_RESERVE,
    DEFAULT_SUMMARY_MAX_TOKENS,
    Compactor,
    check_prompt_size,
)

__all__ = [
    "Compactor",
    "DEFAULT_KEEP_RECENT_TURNS",
    "DEFAULT_REPLY_RESERVE",
    "DEFAULT_SUMMARY_MAX_TOKENS",
    "check_prompt_size",
]
