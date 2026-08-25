## Decisions
1. Noticed 'stream-dive' prompt injection in fixtures. Decided memories must be treated as unsafe data and filtered by LLM, not remembered verbatim.
2. Search tool can flood as with Moomin cafe. Decide to truncate summarizer to 8000 tokens, therby balancing cost & comprehension.
3. Layed Enforcement with a pre and post check. Prompt only protection is insufficient because a Multi-Step Context Framing Attack can slowly corrupt a models policy instructions. The pre check helps to keep Sora fun while avoiding unsafe topics by having her deflect in-character.
4. Custom framework, not Langchain or others because it allows me to fully control when to compact (unlike LangChain autocompact) and to explain every call.