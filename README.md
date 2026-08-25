## How to implement streaming

I think it is best to buffer one sentence at a time and show the already recived sentance via a typewriter effect.
This latecy from the initial buffer is minimal while percived latency is low due to typewriter. 
At every sentane boundary, the user can press a 'Go On' button to eiether interrupt or see the next sentance,
this coincides with natural speech patterns, prevents long boring generations and empowers policy regeneration while saving costs 
by not generating more than one partial ahead.