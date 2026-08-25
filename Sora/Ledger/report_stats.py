"""Read out/trace.jsonl, report what we spent.

    python -m Sora.Ledger.report_stats                     totals + per category
    python -m Sora.Ledger.report_stats --by tag            group differently
    python -m Sora.Ledger.report_stats --category chat     filter, then report
    python -m Sora.Ledger.report_stats --last 1            newest entry verbatim
    python -m Sora.Ledger.report_stats --reconcile         tokenizer drift
    python -m Sora.Ledger.report_stats --compare-cache before after
    python -m Sora.Ledger.report_stats --json              machine readable

Grouping keys: category, tag, session, model, lane, run, day, event.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Iterable

from . import config, trace
from .tokenizer import common_prefix_len

GROUPERS = {
    "category": lambda r: r.get("category") or "?",
    "tag": lambda r: r.get("tag") or "?",
    "session": lambda r: r.get("session_id") or "?",
    "model": lambda r: (r.get("provider") or {}).get("model_requested") or "?",
    "lane": lambda r: (r.get("cache") or {}).get("lane") or "?",
    "run": lambda r: r.get("run_id") or "?",
    "day": lambda r: (r.get("ts") or "")[:10] or "?",
    "event": lambda r: r.get("event") or "?",
}


# --------------------------------------------------------------------------- io

def load(path=None, tag=None, category=None, session=None, run=None, since=None):
    for rec in trace.read(path):
        if tag and rec.get("tag") != tag:
            continue
        if category and rec.get("category") != category:
            continue
        if session and rec.get("session_id") != session:
            continue
        if run and rec.get("run_id") != run:
            continue
        if since and (rec.get("ts") or "") < since:
            continue
        yield rec


def _blank():
    return {
        "calls": 0, "tool_calls": 0, "errors": 0,
        "prompt_tokens": 0, "completion_tokens": 0, "cached_prompt_tokens": 0,
        "local_prompt_tokens": 0, "local_completion_tokens": 0,
        "usd": 0.0, "unpriced_calls": 0, "latency_ms": 0.0,
        "prefix_identical_chars": 0, "prefix_prompt_chars": 0, "prefix_pairs": 0,
        "provider_reports_cache": 0,
    }


def accumulate(acc: dict, rec: dict) -> dict:
    if rec.get("event") == "tool_call":
        acc["tool_calls"] += 1
        acc["latency_ms"] += (rec.get("latency") or {}).get("total_ms") or 0.0
        if not ((rec.get("tool") or {}).get("ok", True)):
            acc["errors"] += 1
        return acc
    if rec.get("event") != "llm_call":
        return acc

    tokens = rec.get("tokens") or {}
    prov = tokens.get("provider") or {}
    loc = tokens.get("local") or {}
    cache = rec.get("cache") or {}
    cost = rec.get("cost") or {}

    acc["calls"] += 1
    if (rec.get("response") or {}).get("error"):
        acc["errors"] += 1
    acc["prompt_tokens"] += prov.get("prompt") or 0
    acc["completion_tokens"] += prov.get("completion") or 0
    acc["cached_prompt_tokens"] += prov.get("cached_prompt") or 0
    acc["local_prompt_tokens"] += loc.get("prompt") or 0
    acc["local_completion_tokens"] += loc.get("completion") or 0
    acc["latency_ms"] += (rec.get("latency") or {}).get("total_ms") or 0.0
    if cache.get("provider_reports_cache"):
        acc["provider_reports_cache"] += 1
    usd = cost.get("usd")
    if usd is None:
        acc["unpriced_calls"] += 1
    else:
        acc["usd"] += usd

    prefix = cache.get("prefix") or {}
    if prefix.get("identical_chars") is not None and prefix.get("prompt_chars"):
        acc["prefix_identical_chars"] += prefix["identical_chars"]
        acc["prefix_prompt_chars"] += prefix["prompt_chars"]
        acc["prefix_pairs"] += 1
    return acc


def finish(acc: dict) -> dict:
    out = dict(acc)
    out["usd"] = round(acc["usd"], 6)
    out["total_tokens"] = acc["prompt_tokens"] + acc["completion_tokens"]
    out["cache_hit_rate"] = (
        acc["cached_prompt_tokens"] / acc["prompt_tokens"] if acc["prompt_tokens"] else None
    )
    out["prefix_identical_ratio"] = (
        acc["prefix_identical_chars"] / acc["prefix_prompt_chars"]
        if acc["prefix_prompt_chars"] else None
    )
    out["avg_latency_ms"] = (
        acc["latency_ms"] / (acc["calls"] + acc["tool_calls"])
        if (acc["calls"] + acc["tool_calls"]) else None
    )
    out["usd_per_call"] = acc["usd"] / acc["calls"] if acc["calls"] else None
    return out


# ------------------------------------------------------------------- formatting

def _pct(x):
    return "n/a" if x is None else "%.1f%%" % (100 * x)


def _usd(x):
    return "n/a" if x is None else "$%.6f" % x


def print_totals(t: dict, title: str = "TOTALS") -> None:
    print("=" * 78)
    print(title)
    print("=" * 78)
    print("  llm calls        : %d   (errors: %d)" % (t["calls"], t["errors"]))
    print("  tool calls       : %d" % t["tool_calls"])
    print("  prompt tokens    : %-10d (provider)   %d (local count)"
          % (t["prompt_tokens"], t["local_prompt_tokens"]))
    print("  completion tokens: %-10d (provider)   %d (local count)"
          % (t["completion_tokens"], t["local_completion_tokens"]))
    print("  total tokens     : %d" % t["total_tokens"])
    print("  cost             : %s%s" % (
        _usd(t["usd"]),
        "   (%d call(s) with no price)" % t["unpriced_calls"] if t["unpriced_calls"] else ""))
    print("  cost / llm call  : %s" % _usd(t["usd_per_call"]))
    print("  cache hit rate   : %s  (%d cached prompt tokens, provider-reported)"
          % (_pct(t["cache_hit_rate"]), t["cached_prompt_tokens"]))
    print("  prompt prefix    : %s byte-identical vs. previous call in lane (%d pairs)"
          % (_pct(t["prefix_identical_ratio"]), t["prefix_pairs"]))
    print("  avg latency      : %s"
          % ("n/a" if t["avg_latency_ms"] is None else "%.0f ms" % t["avg_latency_ms"]))


_COLS = ("calls", "tools", "prompt", "completion", "cached", "hit%", "prefix%", "usd")


def print_table(groups: dict, key_name: str) -> None:
    print()
    print("BY %s" % key_name.upper())
    header = "  %-22s %6s %6s %10s %11s %9s %8s %8s %12s" % (
        key_name, "calls", "tools", "prompt", "completion", "cached", "hit%", "prefix%", "usd")
    print(header)
    print("  " + "-" * (len(header) - 2))
    for name in sorted(groups, key=lambda k: -groups[k]["usd"]):
        g = groups[name]
        print("  %-22s %6d %6d %10d %11d %9d %8s %8s %12s" % (
            name[:22], g["calls"], g["tool_calls"], g["prompt_tokens"], g["completion_tokens"],
            g["cached_prompt_tokens"], _pct(g["cache_hit_rate"]).rstrip("%"),
            _pct(g["prefix_identical_ratio"]).rstrip("%"), _usd(g["usd"])))


def print_entry(rec: dict) -> None:
    print("-" * 78)
    if rec.get("event") == "tool_call":
        tool = rec.get("tool") or {}
        print("tool_call  %s  turn=%s  %s  %.0fms  ok=%s" % (
            rec.get("ts"), rec.get("turn"), tool.get("name"),
            (rec.get("latency") or {}).get("total_ms") or 0, tool.get("ok")))
        print("  args   : %s" % json.dumps(tool.get("args"), ensure_ascii=False)[:400])
        print("  result : %d chars / ~%s local tokens" % (
            tool.get("result_chars") or 0, tool.get("result_tokens_local")))
        print("  %s" % (tool.get("result") or "")[:600])
        return

    tokens = rec.get("tokens") or {}
    print("llm_call   %s  seq=%s  call_id=%s" % (rec.get("ts"), rec.get("seq"), rec.get("call_id")))
    print("  session=%s turn=%s category=%s tag=%s lane=%s" % (
        rec.get("session_id"), rec.get("turn"), rec.get("category"), rec.get("tag"),
        (rec.get("cache") or {}).get("lane")))
    print("  model=%s -> served=%s  finish=%s  latency=%.0fms" % (
        (rec.get("provider") or {}).get("model_requested"),
        (rec.get("provider") or {}).get("model_served"),
        (rec.get("provider") or {}).get("finish_reason"),
        (rec.get("latency") or {}).get("total_ms") or 0))
    print("  sampling: %s" % json.dumps(rec.get("sampling"), ensure_ascii=False)[:300])
    print("  tokens  : provider %s | local %s" % (
        json.dumps(tokens.get("provider"), ensure_ascii=False),
        json.dumps({k: v for k, v in (tokens.get("local") or {}).items()
                    if k in ("prompt", "completion", "encoding")}, ensure_ascii=False)))
    print("  reconcile: %s" % json.dumps(tokens.get("reconciliation"), ensure_ascii=False))
    print("  components (local tokens, sums to local prompt):")
    for name, n in sorted((tokens.get("components") or {}).items(), key=lambda kv: -kv[1]):
        print("      %-14s %6d" % (name, n))
    print("      %-14s %6s / ceiling %s" % (
        "headroom", tokens.get("headroom"), tokens.get("ceiling")))
    cache = rec.get("cache") or {}
    prefix = cache.get("prefix") or {}
    print("  cache   : provider hit %s (%s cached tok) | prefix identical %s of %s chars (%s)" % (
        _pct(cache.get("provider_hit_rate")), cache.get("provider_cached_tokens"),
        prefix.get("identical_chars"), prefix.get("prompt_chars"),
        _pct(prefix.get("identical_ratio"))))
    cost = rec.get("cost") or {}
    print("  cost    : %s (source=%s, table cross-check %s)" % (
        _usd(cost.get("usd")), cost.get("source"), _usd(cost.get("table_usd"))))
    print("  messages sent (%d):" % len(((rec.get("request") or {}).get("messages") or [])))
    for m in ((rec.get("request") or {}).get("messages") or []):
        body = m.get("content")
        if not isinstance(body, str):
            body = json.dumps(body, ensure_ascii=False)
        body = (body or "").replace("\n", " ")
        extra = ""
        if m.get("tool_calls"):
            extra = "  +tool_calls=%s" % json.dumps(m["tool_calls"], ensure_ascii=False)[:200]
        print("      [%-9s] %s%s" % (m.get("role"), body[:220], extra))
    resp = rec.get("response") or {}
    print("  reply   : %s" % (resp.get("text") or "")[:400].replace("\n", " "))
    if resp.get("tool_calls"):
        print("  asked for tools: %s" % json.dumps(resp["tool_calls"], ensure_ascii=False)[:400])
    if resp.get("error"):
        print("  ERROR   : %s" % json.dumps(resp["error"], ensure_ascii=False))


# ------------------------------------------------------------------- sub-reports

def reconciliation(records: Iterable[dict]) -> dict:
    """Local tokenizer vs. provider usage (ASSIGNMENT.md section 2)."""
    rows, encodings = [], {}
    for rec in records:
        if rec.get("event") != "llm_call":
            continue
        tokens = rec.get("tokens") or {}
        prov, loc = tokens.get("provider") or {}, tokens.get("local") or {}
        if not isinstance(prov.get("prompt"), int) or not isinstance(loc.get("prompt"), int):
            continue
        encodings[loc.get("encoding")] = encodings.get(loc.get("encoding"), 0) + 1
        rows.append((loc["prompt"], prov["prompt"], loc.get("completion"), prov.get("completion"),
                     bool(rec.get("request", {}).get("tools"))))
    if not rows:
        return {}
    def stats(pairs):
        pairs = [(l, p) for l, p in pairs if isinstance(l, int) and isinstance(p, int) and p]
        if not pairs:
            return None
        deltas = [l - p for l, p in pairs]
        pcts = [100.0 * (l - p) / p for l, p in pairs]
        return {
            "n": len(pairs),
            "mean_delta": sum(deltas) / len(deltas),
            "mean_error_pct": sum(pcts) / len(pcts),
            "max_abs_delta": max(abs(d) for d in deltas),
            "local_total": sum(l for l, _ in pairs),
            "provider_total": sum(p for _, p in pairs),
        }
    return {
        "encodings": encodings,
        "prompt": stats([(r[0], r[1]) for r in rows]),
        "completion": stats([(r[2], r[3]) for r in rows]),
        "prompt_with_tools": stats([(r[0], r[1]) for r in rows if r[4]]),
        "prompt_without_tools": stats([(r[0], r[1]) for r in rows if not r[4]]),
    }


def print_reconciliation(rec: dict) -> None:
    print()
    print("TOKENIZER RECONCILIATION  (local count vs. provider usage)")
    if not rec:
        print("  no calls with both counts present.")
        return
    print("  counted with: %s" % ", ".join("%s x%d" % (k, v) for k, v in rec["encodings"].items()))
    for label in ("prompt", "completion", "prompt_with_tools", "prompt_without_tools"):
        s = rec.get(label)
        if not s:
            continue
        print("  %-21s n=%-4d mean delta %+7.1f tok  (%+.2f%%)  worst %d tok   local %d vs provider %d"
              % (label, s["n"], s["mean_delta"], s["mean_error_pct"], s["max_abs_delta"],
                 s["local_total"], s["provider_total"]))
    p = rec.get("prompt")
    if p and p["provider_total"]:
        print("  calibration factor (provider/local) for prompt tokens: %.4f"
              % (p["provider_total"] / max(p["local_total"], 1)))


def cache_compare(records, tag_a, tag_b, key="tag"):
    """Before/after cache comparison.

    Two numbers per side, because ASSIGNMENT.md 5.4 accepts either:
      - the provider's cached-token hit rate, when it reports one;
      - the fraction of the prompt that stays byte-identical between
        consecutive calls, recomputed here from the stored prompts so it works
        across processes (each replay is its own process).
    """
    sides = {tag_a: [], tag_b: []}
    keyfn = GROUPERS[key]
    for rec in records:
        if rec.get("event") != "llm_call":
            continue
        k = keyfn(rec)
        if k in sides:
            sides[k].append(rec)

    out = {}
    for name, recs in sides.items():
        acc = _blank()
        for r in recs:
            accumulate(acc, r)
        summary = finish(acc)
        summary["recomputed_prefix_ratio"] = _recompute_prefix(recs)
        out[name] = summary
    return out


def _recompute_prefix(records):
    """Turn-to-turn byte-identical prompt fraction, from the stored prompts.

    Grouped by (run, session, lane) and ordered by seq, so consecutive means
    consecutive *within one conversation*, not across the file.
    """
    from .tokenizer import render_prompt

    lanes: dict = {}
    for rec in records:
        lane = ((rec.get("cache") or {}).get("lane"), rec.get("session_id"), rec.get("run_id"))
        lanes.setdefault(lane, []).append(rec)
    shared_total = current_total = 0
    for lane, recs in lanes.items():
        recs.sort(key=lambda r: r.get("seq") or 0)
        prev = None
        for rec in recs:
            req = rec.get("request") or {}
            rendered = render_prompt(req.get("messages") or [], req.get("tools"))
            if prev is not None and rendered:
                shared_total += common_prefix_len(prev, rendered)
                current_total += len(rendered)
            prev = rendered
    return shared_total / current_total if current_total else None


def print_cache_compare(cmp: dict) -> None:
    print()
    print("CACHE COMPARISON")
    header = "  %-18s %6s %10s %9s %10s %12s %12s" % (
        "tag", "calls", "prompt", "cached", "hit%", "prefix% (live)", "prefix% (recomputed)")
    print(header)
    print("  " + "-" * (len(header) - 2))
    for name, s in cmp.items():
        print("  %-18s %6d %10d %9d %10s %14s %20s" % (
            name[:18], s["calls"], s["prompt_tokens"], s["cached_prompt_tokens"],
            _pct(s["cache_hit_rate"]), _pct(s["prefix_identical_ratio"]),
            _pct(s["recomputed_prefix_ratio"])))
    names = list(cmp)
    if len(names) == 2:
        a, b = (cmp[n] for n in names)
        for label, field in (("provider hit rate", "cache_hit_rate"),
                             ("byte-identical prefix", "recomputed_prefix_ratio")):
            if a.get(field) is not None and b.get(field) is not None:
                print("  delta %-22s %s -> %s  (%+.1f pp)" % (
                    label, _pct(a[field]), _pct(b[field]), 100 * (b[field] - a[field])))
            else:
                print("  delta %-22s not comparable (missing on one side)" % label)


# -------------------------------------------------------------------------- main

def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp932/cp950
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="Report LLM cost/tokens/cache from the trace.")
    ap.add_argument("--trace", default=None, help="path to trace.jsonl (default out/trace.jsonl)")
    ap.add_argument("--by", default="category", choices=sorted(GROUPERS),
                    help="grouping for the breakdown table (default: category)")
    ap.add_argument("--category", help="only calls in this category")
    ap.add_argument("--tag", help="only calls with this experiment tag")
    ap.add_argument("--session", help="only calls from this session id")
    ap.add_argument("--run", help="only calls from this run id")
    ap.add_argument("--since", help="only records with ts >= this ISO timestamp")
    ap.add_argument("--last", nargs="?", type=int, const=1, default=0,
                    metavar="N", help="print the last N entries verbatim (default 1)")
    ap.add_argument("--tools", action="store_true", help="include tool_call events in --last")
    ap.add_argument("--reconcile", action="store_true", help="tokenizer vs. provider usage")
    ap.add_argument("--compare-cache", nargs="*", metavar="TAG",
                    help="compare cache metrics between two tags (default: the two most recent)")
    ap.add_argument("--no-table", action="store_true", help="totals only")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = ap.parse_args(argv)

    path = args.trace or config.trace_path()
    records = list(load(path, tag=args.tag, category=args.category,
                        session=args.session, run=args.run, since=args.since))
    if not records:
        print("No trace records in %s (nothing has been metered yet)." % path)
        return 1

    total = _blank()
    groups: dict = {}
    keyfn = GROUPERS[args.by]
    for rec in records:
        accumulate(total, rec)
        g = groups.setdefault(keyfn(rec), _blank())
        accumulate(g, rec)
    total = finish(total)
    groups = {k: finish(v) for k, v in groups.items()}

    compare = None
    if args.compare_cache is not None:
        tags = args.compare_cache
        if len(tags) < 2:
            seen = []
            for rec in records:
                if rec.get("tag") and rec["tag"] not in seen:
                    seen.append(rec["tag"])
            tags = (tags + [t for t in seen if t not in tags])[:2]
        if len(tags) < 2:
            print("Need two tags to compare; the trace only has %s. "
                  "Re-run with SORA_LEDGER_TAG=<name> to tag a run." % (tags or "none"))
        else:
            compare = cache_compare(records, tags[0], tags[1])

    recon = reconciliation(records) if args.reconcile else None

    if args.json:
        print(json.dumps({
            "trace": str(path), "totals": total, "by": {args.by: groups},
            "reconciliation": recon, "cache_compare": compare,
        }, ensure_ascii=False, indent=2, default=str))
        return 0

    print_totals(total, "TOTALS  (%s)" % path)
    if not args.no_table:
        print_table(groups, args.by)
    if recon is not None:
        print_reconciliation(recon)
    if compare:
        print_cache_compare(compare)
    if args.last:
        wanted = [r for r in records if args.tools or r.get("event") != "tool_call"]
        print()
        print("LAST %d ENTR%s" % (min(args.last, len(wanted)),
                                  "Y" if args.last == 1 else "IES"))
        for rec in wanted[-args.last:]:
            print_entry(rec)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
