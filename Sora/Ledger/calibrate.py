"""Measure our local tokenizer against the provider's, with real calls.

ASSIGNMENT.md section 2 asks us to name the tokenizer our budget numbers come
from and reconcile it against provider-reported usage. `report_stats
--reconcile` does that passively over whatever the agent happened to send;
this does it actively, with probes designed so each one isolates a single
unknown:

    empty        one 1-char user message      -> fixed per-request overhead
    two_messages the same message twice       -> per-message framing
    long_user    a long user message          -> raw text accounting
    tools        the agent's real tool schemas-> tool-schema rendering cost
    tool_result  a tool-role message          -> tool-result framing

The tool-schema cost is the interesting one: OpenAI re-renders function
schemas into a compact form before counting, so counting the JSON we send
overcounts badly. `tools` minus `empty` is the true cost of the agent's tool
block; the ratio against our estimate is written to
out/tokenizer_calibration.json and applied by tokenizer.count_tools().

    python -m Sora.Ledger.calibrate            # ~6 calls, well under a cent
    python -m Sora.Ledger.calibrate --no-write # measure, do not apply

Re-run it whenever LLM_MODEL changes: the correction is model-specific and is
stored with the model id it was measured on.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from . import config, tokenizer
from .meter import call_context


def _probes():
    from baseline.agent import TOOLS  # the schemas we actually ship

    long_text = ("High-fidelity data about themed cafes in Taipei, Senpai. " * 40)
    return [
        ("empty", [{"role": "user", "content": "x"}], None),
        ("two_messages", [{"role": "user", "content": "x"},
                          {"role": "assistant", "content": "x"}], None),
        ("long_user", [{"role": "user", "content": long_text}], None),
        ("tools", [{"role": "user", "content": "x"}], TOOLS),
        ("one_tool", [{"role": "user", "content": "x"}], TOOLS[:1]),
        ("tool_result", [{"role": "user", "content": "x"},
                         {"role": "assistant", "content": "",
                          "tool_calls": [{"id": "call_abc123", "type": "function",
                                          "function": {"name": "web_search",
                                                       "arguments": '{"query": "x"}'}}]},
                         {"role": "tool", "tool_call_id": "call_abc123",
                          "content": long_text}], TOOLS),
    ]


def run(write: bool = True) -> dict:
    from llm.client import chat, model_name

    model = model_name()
    results = {}
    with call_context(category="eval", tag="calibration", session_id="tokenizer_calibration",
                      cache_lane="calibration", note="tokenizer calibration probe"):
        for name, messages, tools in _probes():
            # max_tokens=1: we are paying for the prompt count, not a reply.
            resp = chat(messages, tools=tools, max_tokens=1, temperature=0)
            provider = getattr(resp, "usage", None)
            provider_prompt = getattr(provider, "prompt_tokens", None)
            local = tokenizer.count_prompt(messages, tools=tools, model=model)
            results[name] = {
                "provider_prompt_tokens": provider_prompt,
                "local_prompt_tokens": local["total"],
                "local_components": local["components"],
                "delta": (local["total"] - provider_prompt) if provider_prompt else None,
            }

    out = {
        "model": model,
        "encoding": tokenizer.effective_encoding_name(model),
        "method": tokenizer.METHOD,
        "measured_at": _now(),
        "probes": results,
    }

    empty = results["empty"]["provider_prompt_tokens"]
    tools_probe = results["tools"]["provider_prompt_tokens"]
    two = results["two_messages"]["provider_prompt_tokens"]
    if empty and tools_probe:
        provider_tool_cost = tools_probe - empty
        # Our estimate for the same block, with any previous scale removed.
        local_tool_cost = results["tools"]["local_components"].get("tool_schemas", 0)
        prior = tokenizer._calibration().get("tool_schema_scale") or 1.0
        raw_local = local_tool_cost / prior
        out["tool_schema_provider_tokens"] = provider_tool_cost
        out["tool_schema_local_tokens"] = round(raw_local, 1)
        out["tool_schema_scale"] = round(provider_tool_cost / raw_local, 4) if raw_local else None
    if empty and two:
        # Apples to apples: the provider's cost of one extra message vs. ours.
        out["extra_message_tokens_provider"] = two - empty
        out["extra_message_tokens_local"] = (
            results["two_messages"]["local_prompt_tokens"] - results["empty"]["local_prompt_tokens"])
    # Probes with no tools isolate the framing constants; if those are exact,
    # any remaining drift is the tool block and nothing else.
    out["text_only_probes_exact"] = all(
        results[name]["delta"] == 0 for name in ("empty", "two_messages", "long_user")
    )

    if write and out.get("tool_schema_scale"):
        path = pathlib.Path(config.trace_path()).parent / "tokenizer_calibration.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        out["written_to"] = str(path)
    return out


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Calibrate the local tokenizer against the provider.")
    ap.add_argument("--no-write", action="store_true",
                    help="measure and print, but do not write the correction file")
    args = ap.parse_args(argv)

    out = run(write=not args.no_write)
    print("model    : %s" % out["model"])
    print("encoding : %s (%s)" % (out["encoding"], out["method"]))
    print()
    print("  %-14s %10s %10s %8s" % ("probe", "provider", "local", "delta"))
    print("  " + "-" * 46)
    for name, r in out["probes"].items():
        print("  %-14s %10s %10s %8s" % (
            name, r["provider_prompt_tokens"], r["local_prompt_tokens"], r["delta"]))
    print()
    if out.get("tool_schema_scale"):
        print("tool schemas: provider charges %d tokens, we estimated %.0f -> scale %.4f"
              % (out["tool_schema_provider_tokens"], out["tool_schema_local_tokens"],
                 out["tool_schema_scale"]))
    if out.get("extra_message_tokens_provider") is not None:
        print("one extra message costs: provider %s tokens, our count %s"
              % (out["extra_message_tokens_provider"], out["extra_message_tokens_local"]))
    print("text-only probes exact: %s%s" % (
        out["text_only_probes_exact"],
        "" if out["text_only_probes_exact"] else "  <- framing constants need work"))
    if out.get("written_to"):
        print("written to %s (applied automatically by tokenizer.count_tools)" % out["written_to"])
    else:
        print("not written (--no-write, or nothing to correct)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
