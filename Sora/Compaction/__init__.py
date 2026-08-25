'''
Cache friendly compaction
=========================

We will need to maintain a strict 8000 token ceiling on all agents.

To do this, we will consider the headroom, and if we are close to, or hit the
ceiling, then begin compaction. 
We will need to ahere to section 5.1 **Per-turn budget table.** of the Assignment.

However, we do not want to compact Sora's prompt because that would damage her
personality, therefore compaction will only compact the chat history, not the Sora prompt.

So before we might have a context state like:
[persona prompt]
[Big chat history]


and after compaction we would have:
[persona prompt]
[compact chat history]

Note also that this is cache friendly because the persona prefix stay fixed.

Furthermore, to Measure that our compaction did not damage Sora Persona, we will
use the 'personality' Judge to check that her personality scores before and after are approximately equal,
this may require some statistical averaging.

'''