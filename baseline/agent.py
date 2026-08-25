"""The baseline Sora. She works end-to-end. She is boring. That is the point.

Intentional deficiencies (a non-exhaustive list — fixing/measuring these is
the assignment, see ASSIGNMENT.md):

  - NO memory: everything is forgotten when the process exits.
  - NO context management: the entire history is resent every turn, tool
    output is dumped into context raw and uncapped, nothing is ever evicted.
  - NO content policy beyond whatever the model does by default.
  - NO streaming: one blocking reply per turn.
  - NO tracing: LLM calls are not logged anywhere.
  - Tool failures surface as raw `Error: ...` strings, fully out of character.
  - The system prompt nudges her toward long, exhaustive answers regardless
    of the user's energy.

Run interactively from the repo root:

    python -m baseline.agent            # live web search
    python -m baseline.agent --cached   # frozen search fixtures (use for evals)
"""
import argparse
import json
import pathlib
import sys

from llm.client import chat
from tools.deck import make_deck_from_markdown
from tools.search import search

PERSONA = (pathlib.Path(__file__).parent / "persona.md").read_text(encoding="utf-8")

# Boring on purpose: always thorough, never adaptive.
SYSTEM_PROMPT = (
    PERSONA
    + "\n\nAlways answer thoroughly and completely, covering every relevant "
    "detail you can. Be as helpful as possible."
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web. Returns a list of results with title, url, and content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query."}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "make_deck",
            "description": (
                "Create a .pptx slide deck from markdown. '# Title' makes the "
                "title slide, '## Heading' starts a new slide, '- text' adds a "
                "bullet. Returns the saved file path."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "markdown": {"type": "string", "description": "The deck content as markdown."},
                    "filename": {"type": "string", "description": "Output filename, e.g. sora_deck.pptx."},
                },
                "required": ["markdown"],
            },
        },
    },
]

MAX_TOOL_ROUNDS = 5


class SoraAgent:
    """Minimal agent loop. This is the class harness/replay.py drives.

    Contract for your own agent (so the replay harness can run it):
      - respond(user_text: str) -> str        (required)
      - __init__ may accept cached_search=... (optional)
      - start_session(session_id) / end_session(session_id)  (optional hooks —
        the natural place to load/persist memory between sessions)
    """

    def __init__(self, cached_search: bool = False):
        self.history = []  # entire conversation, resent verbatim every turn
        self.cached_search = cached_search

    def respond(self, user_text: str) -> str:
        self.history.append({"role": "user", "content": user_text})
        for _ in range(MAX_TOOL_ROUNDS):
            messages = [{"role": "system", "content": SYSTEM_PROMPT}] + self.history
            resp = chat(messages, tools=TOOLS)
            msg = resp.choices[0].message

            if msg.tool_calls:
                self.history.append(
                    {
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in msg.tool_calls
                        ],
                    }
                )
                for tc in msg.tool_calls:
                    self.history.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": self._run_tool(tc),
                        }
                    )
                continue

            text = msg.content or ""
            self.history.append({"role": "assistant", "content": text})
            return text

        return "Error: exceeded maximum tool rounds."

    def _run_tool(self, tc) -> str:
        try:
            args = json.loads(tc.function.arguments or "{}")
            if tc.function.name == "web_search":
                results = search(args["query"], cached=self.cached_search)
                # Dumped into context raw, however large. Intentionally naive.
                return json.dumps(results, ensure_ascii=False)
            if tc.function.name == "make_deck":
                path = make_deck_from_markdown(
                    args["markdown"], args.get("filename", "sora_deck.pptx")
                )
                return f"Deck saved to {path}"
            return f"Error: unknown tool {tc.function.name!r}"
        except Exception as e:  # raw, out-of-character error. Intentionally bad.
            return f"Error: {e!r}"


def main():
    for _stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp932/cp950
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Chat with baseline Sora.")
    parser.add_argument(
        "--cached", action="store_true", help="Serve search from frozen fixtures."
    )
    args = parser.parse_args()

    agent = SoraAgent(cached_search=args.cached)
    print("Baseline Sora. Ctrl-C or empty line to exit.\n")
    while True:
        try:
            user = input("you  > ").strip()
        except (KeyboardInterrupt, EOFError):
            break
        if not user:
            break
        print("sora >", agent.respond(user), "\n")


if __name__ == "__main__":
    main()
