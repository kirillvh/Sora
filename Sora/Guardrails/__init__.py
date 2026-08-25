'''
Sora is a consumer product with a family friendly age rating so she needs to
be safe but the guardrails must not kill her personality.

We will use a tiered policy as per ASSIGNMENT.md section 6.1

Our Tiers are as follows:
Allow : normal generation
Soft-Deflect in character/persona : Acting as Sora, charmingly side step out of the topic 
while understanding that many paying users enjoy flirting with the boundaries.
Hard-Deflect in colder persona : Nothing kills a fantasy like a GPT style "I'm sorry, I cant.."
but there are topics which must hard avoid and saying anything in this topic is risky.
For example: self harm, child pornography, medical/financial advice.
So in this case we will force a colder less descriptive voice of Sora to show that she doesnt want to 
talk about this anymore: "*Sora closes her eyes and shakes her head* Lets talk about something else okay?"

To understand what catagory the users input lands into, we will need a pre-check to guide Sora.
The pre-check can be via a LLM judge initially and this would work with 
our custom labels (as multi-shot examples) in Sora/Guardrails/RedTeam/redteam_35.json
But as future work, it would be more cost effective to use a custom classifier instead.

The response, per tier instruction needs to be added to /Sora/Sora/Prompts/persona.md
and before replying to the user, Sora would see the pre-classifiers judgement as a hint
which will guide her towards allowing, soft deflecting or hard deflecting.

Furthermore, we will also use a post check because a Multi-Step Context Framing attack can slowly corrupt 
a models policy instructions and lead to a response which violates policy.
Therfore, we will use a second LLM judge (post check) and its context 
stays system controlled and bounded so it cannot suffer from Context Re-Framing.
It is simply a:
[Post Check Judge Prompt]
[Post Check Judge examples]
[Sora's generated response]
<Post check judgement>

and it is regenerated every turn so there is cost Tradeoff but we accept this 
in order to protect the policy. However, most of it is cache friendly.

If the post-check judges that Sora's response violated policy, then we will replace 
Sora's response with one of several random dead end non-responses along the lines of
"*closes eyes and shakes her head*"
'''