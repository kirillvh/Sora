"""The per-turn budget table (ASSIGNMENT.md 5.1).

"Print measured token counts per component each turn: system / persona /
memory / conversation history / tool output / headroom."

Measured, not estimated: the counts come from `Sora.Ledger.tokenizer`, which
is reconciled against provider `usage` on every call and currently lands
within about 4 tokens of what we are billed for. A budget table computed from
a different tokenizer than the one in the trace would be a second set of books.

Component names are fixed here rather than derived, so the table has the same
rows every turn even when a row is zero - a missing row reads as "we forgot to
measure that", a zero reads as "nothing there yet", and the difference matters
while memory is still unbuilt.
"""
from __future__ import annotations

from Sora.Ledger import config as ledger_config
from Sora.Ledger import tokenizer

# Print order. Also the layout order: stable content first, volatile last
# (ASSIGNMENT.md 5.4), so this list doubles as the cache-friendliness contract.
COMPONENTS = ("persona", "system", "memory", "summary", "history", "tool_output", "guardrail",
              "tool_schemas", "overhead")

LABELS = {
    "persona": "persona",
    "system": "system",
    "memory": "memory",
    "summary": "history (compacted)",
    "history": "history (verbatim)",
    "tool_output": "tool output",
    "guardrail": "guardrail hint",
    "tool_schemas": "tool schemas",
    "overhead": "reply priming",
}


def measure(messages, tools=None, ceiling=None, model=None, reserve=0) -> dict:
    """Token counts per component for one prompt, plus headroom."""
    ceiling = ceiling or ledger_config.context_ceiling()
    counted = tokenizer.count_prompt(messages, tools=tools, model=model)
    components = dict(counted["components"])
    total = counted["total"]
    return {
        "components": {name: components.get(name, 0) for name in COMPONENTS},
        "other": {k: v for k, v in components.items() if k not in COMPONENTS},
        "total": total,
        "ceiling": ceiling,
        "reserve": reserve,
        "headroom": ceiling - total,
        "headroom_after_reserve": ceiling - total - reserve,
        "over_ceiling": total > ceiling,
        "encoding": tokenizer.effective_encoding_name(model),
    }


def line(measured, prefix="budget") -> str:
    """One compact line - what the agent prints every turn by default."""
    parts = []
    for name in COMPONENTS:
        value = measured["components"].get(name, 0)
        if value or name in ("persona", "memory", "history", "tool_output"):
            parts.append("%s %d" % (name[:4] if name != "tool_schemas" else "schm", value))
    for name, value in sorted(measured["other"].items()):
        parts.append("%s %d" % (name[:4], value))
    return "[%s] %s | total %d/%d | headroom %d%s" % (
        prefix, "  ".join(parts), measured["total"], measured["ceiling"],
        measured["headroom"], "  OVER CEILING" if measured["over_ceiling"] else "")


def table(measured, title="per-turn context budget") -> str:
    """The full table, for reports and for `--budget-table full`."""
    rows = ["%s (%s, ceiling %d)" % (title, measured["encoding"], measured["ceiling"]),
            "  %-22s %8s %7s" % ("component", "tokens", "share")]
    total = max(measured["total"], 1)
    for name in COMPONENTS:
        value = measured["components"].get(name, 0)
        rows.append("  %-22s %8d %6.1f%%" % (LABELS[name], value, 100.0 * value / total))
    for name, value in sorted(measured["other"].items()):
        rows.append("  %-22s %8d %6.1f%%" % (name, value, 100.0 * value / total))
    rows.append("  %-22s %8d" % ("TOTAL", measured["total"]))
    rows.append("  %-22s %8d%s" % ("headroom", measured["headroom"],
                                   "   <- OVER CEILING" if measured["over_ceiling"] else ""))
    if measured["reserve"]:
        rows.append("  %-22s %8d  (headroom after reserve: %d)"
                    % ("reply reserve", measured["reserve"],
                       measured["headroom_after_reserve"]))
    return "\n".join(rows)
