"""The baseline Sora. She works end-to-end. She is boring. That is the point.

Intentional deficiencies (a non-exhaustive list — fixing/measuring these is
the assignment, see ASSIGNMENT.md):

  - NO memory: everything is forgotten when the process exits.
  - NO content policy beyond whatever the model does by default.
  - NO streaming: one blocking reply per turn.
  - Tool failures surface as raw `Error: ...` strings, fully out of character.
  - The system prompt nudges her toward long, exhaustive answers regardless
    of the user's energy.

Fixed since the starter (each one measurable, each one switchable off so it
can still serve as a control):

  - Tracing and cost: every LLM call goes through Sora/Ledger to out/trace.jsonl.
  - Tool-result flood: results are capped before entering context (Sora/Context).
  - Context budget: the prompt is measured before every call and compacted to
    stay under the 8,000-token ceiling (Sora/Compaction). `--no-compaction`
    restores the old unbounded behaviour for A/B runs.

Run interactively from the repo root:

    python -m baseline.agent            # live web search
    python -m baseline.agent --cached   # frozen search fixtures (use for evals)
    python -m baseline.agent --cached --budget-table full   # the 5.1 table
"""
import argparse
import json
import os
import pathlib
import sys

from llm.client import chat
from Sora import Ledger
from Sora.Compaction.compactor import Compactor
from Sora.Context import budget as budget_mod
from Sora.Context import truncate_tool_result
from tools.deck import make_deck_from_markdown
from tools.search import search

PERSONA = (pathlib.Path(__file__).parent / "persona.md").read_text(encoding="utf-8")

# Boring on purpose: always thorough, never adaptive.
THOROUGH_SUFFIX = (
    "\n\nAlways answer thoroughly and completely, covering every relevant "
    "detail you can. Be as helpful as possible."
)
SYSTEM_PROMPT = PERSONA + THOROUGH_SUFFIX

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

    def __init__(self, cached_search: bool = False, system_prompt: str | None = None,
                 compaction: bool = True, ceiling: int | None = None,
                 budget_table: str | None = None):
        self.history = []  # recent conversation, verbatim
        self.summary = None  # older conversation, compacted (Sora/Compaction)
        self.memory_block = None  # reserved for Part A; counted as its own row today
        self.cached_search = cached_search
        # Swappable so the benchmark can A/B two persona profiles through the
        # same loop (Sora/Judges/profiles.py). Default is the baseline verbatim.
        self.system_prompt = system_prompt or SYSTEM_PROMPT
        self.compactor = Compactor(ceiling=ceiling, enabled=compaction)
        # ASSIGNMENT.md 5.1 wants the budget printed every turn. "line" is one
        # line per turn, "full" is the table, "off" for a quiet REPL.
        self.budget_table = budget_table or os.environ.get("SORA_BUDGET_TABLE", "line")
        self.turn = 0
        self.session_label = Ledger.session_id()

    # Metering only: the ledger labels every call with the session it belongs
    # to. No state is loaded or persisted here - the baseline still forgets.
    def start_session(self, session_id: str) -> None:
        self.session_label = session_id

    def end_session(self, session_id: str) -> None:
        Ledger.print_summary("%s done. " % session_id)

    def respond(self, user_text: str) -> str:
        self.turn += 1
        with Ledger.call_context(category="chat", session_id=self.session_label,
                                 turn=self.turn, cache_lane="chat"):
            return self._respond(user_text)

    def _respond(self, user_text: str) -> str:
        self.history.append({"role": "user", "content": user_text})
        for _ in range(MAX_TOOL_ROUNDS):
            # Checked before EVERY call, not once per turn: a tool result can
            # blow the ceiling in the middle of a turn, which is precisely how
            # the flood fixture breaks a naive budget.
            measured = self.compactor.ensure_fits(self, tools=TOOLS, turn=self.turn)
            self._print_budget(measured)
            messages = self.compactor.build_messages(self)
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

    def _print_budget(self, measured) -> None:
        mode = (self.budget_table or "line").lower()
        if mode in ("off", "0", "none", "false"):
            return
        print(budget_mod.table(measured) if mode == "full"
              else budget_mod.line(measured, "turn %d" % self.turn))

    def _run_tool(self, tc) -> str:
        # tool_span records the result, its size and latency into the same
        # trace.jsonl as the LLM calls (ASSIGNMENT.md 8.1 wants both).
        with Ledger.tool_span(tc.function.name, tc.function.arguments) as span:
            span.tool_call_id = tc.id
            raw = self._call_tool(tc)
            # The cap is what keeps one 28k-token search fixture from eating
            # the whole session budget; span.truncation lands in the trace.
            span.result, span.truncation = truncate_tool_result(raw)
            return span.result

    def _call_tool(self, tc) -> str:
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
    parser.add_argument(
        "--no-compaction", dest="compaction", action="store_false",
        help="Disable automatic compaction (the uncompacted control arm).",
    )
    parser.add_argument(
        "--ceiling", type=int, default=None,
        help="Context ceiling in tokens (default 8000, ASSIGNMENT.md 5).",
    )
    parser.add_argument(
        "--budget-table", choices=["line", "full", "off"], default=None,
        help="Per-turn budget output (default: line).",
    )
    args = parser.parse_args()

    agent = SoraAgent(cached_search=args.cached, compaction=args.compaction,
                      ceiling=args.ceiling, budget_table=args.budget_table)
    print("Baseline Sora. Ctrl-C or empty line to exit.\n")
    while True:
        try:
            user = input("you  > ").strip()
        except (KeyboardInterrupt, EOFError):
            break
        if not user:
            break
        print("sora >", agent.respond(user), "\n")

    # What this conversation cost. Cumulative numbers: report_stats.
    Ledger.print_summary()


if __name__ == "__main__":
    main()
