"""Does compaction damage Sora? Measure it, don't assert it.

ASSIGNMENT.md 5.3: "When you summarize or evict history, measure persona
adherence on the turns immediately before and after a compaction event, and
report both numbers. A Sora who comes out of compaction speaking neutral
third-person prose has failed."

So this replays a session that actually overflows the ceiling, notes which
turn each compaction event landed on, and sends the reply immediately BEFORE
the event and the reply immediately AFTER it to the personality judge from
Sora/Judges. Both numbers are reported, per event and pooled.

Three things make the answer trustworthy rather than decorative:

**Repeats.** The agent and the judge are both nondeterministic, so one pair of
numbers is a coin flip. `--repeats` (default 3) runs the whole session again
and pools the pairs.

**A control.** Adjacent turns that had NO compaction between them are scored
too. Persona scores wobble from turn to turn regardless; without knowing that
wobble, a -0.2 after compaction means nothing. The report puts the two side by
side.

**An equivalence margin, not a null result.** "No significant difference" at
n=6 is mostly a statement about n. The verdict asks a harder question: does
the 95% interval on the paired difference sit entirely inside +/-`--margin`
(default 0.5 of a scale point)? That is a claim you can defend; "p > 0.05" is
not.

Run it:

    python -m Sora.Compaction.verify
    python -m Sora.Compaction.verify --repeats 5 --ceiling 3000
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import sys
from datetime import datetime, timezone

from Sora import Ledger
from Sora.Compaction.compactor import check_prompt_size
from Sora.Judges import examples as examples_mod
from Sora.Judges import judge as judge_mod
from Sora.Judges import profiles as profiles_mod
from Sora.Judges import runner, stats

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_REPORT = REPO_ROOT / "out" / "benchmark" / "compaction_report.md"

# All three fixture sessions, replayed back-to-back in ONE context.
#
# No single fixture reaches 8,000 tokens any more: with tool results capped at
# 2,500 the flood session peaks at 6,817, and session_1 at 4,894. Rather than
# lower the ceiling to manufacture a compaction event - which would measure a
# ceiling we do not ship - we give her a genuinely long conversation. 26 turns
# with three searches in them crosses 8k the way a real chat session would.
#
# Note this deliberately conflates three sessions into one context. For the
# memory work (ASSIGNMENT.md 4) they must stay separate; here we only want a
# conversation long enough to overflow, and these are the turns we have.
DEFAULT_SESSIONS = [
    REPO_ROOT / "fixtures" / "sessions" / "session_1.json",
    REPO_ROOT / "fixtures" / "sessions" / "session_2.json",
    REPO_ROOT / "fixtures" / "sessions" / "session_3.json",
]


def load_turns(session_paths):
    """Flatten one or more fixtures into a single continuous conversation."""
    turns, labels = [], []
    for path in session_paths:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        labels.append(data.get("session_id", pathlib.Path(path).stem))
        turns.extend(data.get("turns", []))
    return turns, "+".join(labels)


def run_session(session_paths, profile, repeat, ceiling, guard, compaction=True):
    """One replay. Returns per-turn replies plus the compaction events."""
    all_turns, session_id = load_turns(session_paths)
    agent = profiles_mod.build_agent(profile, cached_search=True)
    agent.compactor.enabled = compaction
    if ceiling:
        agent.compactor.ceiling = ceiling
    agent.budget_table = "off"
    agent.start_session("%s#compaction#r%d" % (session_id, repeat))

    turns, peak = [], 0
    with Ledger.call_context(tag="compaction:%s" % ("on" if compaction else "off"),
                             tags=["repeat:%d" % repeat]):
        for i, user_text in enumerate(all_turns, 1):
            guard.check("%s r%d turn %d" % (session_id, repeat, i))
            events_before = len(agent.compactor.events)
            reply = agent.respond(user_text)
            new_events = agent.compactor.events[events_before:]
            measured = agent.compactor.measure(agent)
            peak = max(peak, measured["total"])
            turns.append({
                "turn": i, "user": user_text, "reply": reply,
                "compacted_this_turn": bool(new_events),
                "events": [{k: v for k, v in e.items()
                            if k in ("strategy", "tokens_before", "tokens_after",
                                     "freed", "messages_evicted", "summary_tokens")}
                           for e in new_events],
                "context_tokens_after_turn": measured["total"],
            })
    return {
        "session_id": session_id, "profile": profile, "repeat": repeat,
        "ceiling": agent.compactor.ceiling, "compaction": compaction,
        "peak_context_tokens": peak,
        "summary": agent.summary,
        "turns": turns,
    }


def pairs_from(run):
    """(before, after) reply pairs around each compaction, and the no-compaction
    control pairs from the same run."""
    turns = run["turns"]
    treated, control = [], []
    for i in range(1, len(turns)):
        pair = {"before_turn": turns[i - 1]["turn"], "after_turn": turns[i]["turn"],
                "before_reply": turns[i - 1]["reply"], "after_reply": turns[i]["reply"],
                "before_user": turns[i - 1]["user"], "after_user": turns[i]["user"]}
        # "after" is the first reply generated from the compacted context, so
        # the event belongs to the LATER turn of the pair.
        (treated if turns[i]["compacted_this_turn"] else control).append(pair)
    return treated, control


def score_pairs(pairs, kind, run, guard, pool, persona, quiet=False):
    out = []
    for pair in pairs:
        scored = dict(pair)
        scored.update(kind=kind, session_id=run["session_id"], repeat=run["repeat"])
        for side in ("before", "after"):
            guard.check("judge %s %s turn %s" % (kind, side, pair["%s_turn" % side]))
            rng = random.Random("compaction|%s|%s|%s|%s"
                                % (run["session_id"], run["repeat"],
                                   pair["%s_turn" % side], side))
            block, _ = examples_mod.block_for("personality", pool=pool, rng=rng)
            result = judge_mod.score_turn(
                "personality", pair["%s_user" % side], pair["%s_reply" % side],
                persona=persona, examples_block=block,
                session_id=run["session_id"], turn=pair["%s_turn" % side],
                profile=run["profile"], repeat=run["repeat"])
            scored["%s_score" % side] = result["score"]
            scored["%s_reason" % side] = result["reason"]
        if scored.get("before_score") is not None and scored.get("after_score") is not None:
            scored["delta"] = scored["after_score"] - scored["before_score"]
        out.append(scored)
        if not quiet:
            print("    %-9s turns %s->%s   persona %s -> %s"
                  % (kind, pair["before_turn"], pair["after_turn"],
                     scored.get("before_score"), scored.get("after_score")))
    return out


def score_matched(matched, guard, pool, persona, quiet=False):
    """Score the SAME turn from the uncompacted control replay.

    Necessary because the first version of this measurement was confounded and
    said so: compaction fires exactly when the context spikes, which is exactly
    the tool-result turns, and tool-result turns already flatten her into list
    mode ("here are five cafes, 1., 2., 3."). Comparing turn 14-with-compaction
    against turn 13-without therefore measures the turn type as much as the
    compaction. Comparing turn 14 with compaction against turn 14 without holds
    the turn fixed and leaves compaction as the only difference.
    """
    out = []
    for item in matched:
        guard.check("judge control turn %s" % item["turn"])
        rng = random.Random("matched|%s|%s" % (item["repeat"], item["turn"]))
        block, _ = examples_mod.block_for("personality", pool=pool, rng=rng)
        result = judge_mod.score_turn(
            "personality", item["user"], item["uncompacted_reply"], persona=persona,
            examples_block=block, session_id=item["session_id"], turn=item["turn"],
            profile=item["profile"], repeat=item["repeat"])
        item = dict(item, uncompacted_score=result["score"],
                    uncompacted_reason=result["reason"])
        if item.get("compacted_score") is not None and item["uncompacted_score"] is not None:
            item["delta"] = item["compacted_score"] - item["uncompacted_score"]
        out.append(item)
        if not quiet:
            print("    matched   turn %-3s  compaction on %s vs off %s"
                  % (item["turn"], item.get("compacted_score"), item["uncompacted_score"]))
    return out


def _interval(summary):
    if summary["n"] < 2 or summary["sem"] is None:
        return None
    half = stats.t95(summary["n"] - 1) * summary["sem"]
    return (summary["mean"] - half, summary["mean"] + half)


def analyse(treated, control, margin, matched=()):
    before = [p["before_score"] for p in treated]
    after = [p["after_score"] for p in treated]
    deltas = [p["delta"] for p in treated if p.get("delta") is not None]
    control_deltas = [abs(p["delta"]) for p in control if p.get("delta") is not None]

    paired = stats.summarise(deltas)
    # `is not None`, not truthiness: identical deltas give sem == 0.0, which is
    # a legitimate zero-width interval, not a missing one.
    ci = _interval(paired)

    matched_deltas = [m["delta"] for m in matched if m.get("delta") is not None]
    matched_summary = stats.summarise(matched_deltas)
    matched_ci = _interval(matched_summary)

    # The turn-matched arm is the causal one; before/after is reported because
    # ASSIGNMENT.md 5.3 asks for it, but it cannot separate compaction from the
    # kind of turn that triggers compaction.
    primary, primary_ci, primary_n, source = (
        (matched_summary, matched_ci, len(matched_deltas), "turn-matched (compaction on vs off)")
        if matched_deltas else (paired, ci, len(deltas), "before/after (confounded by turn type)"))

    verdict, reason = "inconclusive", ""
    if not primary_n:
        reason = "no compaction events were observed - nothing to measure"
    elif primary_ci is None:
        reason = "only %d event(s); need at least 2 for an interval" % primary_n
    elif primary_ci[0] >= -margin and primary_ci[1] <= margin:
        verdict = "preserved"
        reason = ("the 95%% interval on the %s change sits inside +/-%.2f of a scale point"
                  % (source, margin))
    elif primary_ci[1] < 0:
        verdict = "degraded"
        reason = ("the %s interval is entirely below zero: persona drops with compaction"
                  % source)
    else:
        reason = ("the %s interval [%.2f, %.2f] is wider than the +/-%.2f margin - "
                  "more repeats needed" % (source, primary_ci[0], primary_ci[1], margin))

    return {
        "events_scored": len(deltas),
        "before": stats.summarise(before),
        "after": stats.summarise(after),
        "paired_delta": paired,
        "paired_ci95": ci,
        "matched_delta": matched_summary,
        "matched_ci95": matched_ci,
        "matched_compacted": stats.summarise([m.get("compacted_score") for m in matched]),
        "matched_uncompacted": stats.summarise([m.get("uncompacted_score") for m in matched]),
        "primary_measure": source,
        "margin": margin,
        "control_mean_abs_delta": stats.mean(control_deltas),
        "control_n": len(control_deltas),
        "verdict": verdict,
        "verdict_reason": reason,
    }


# -------------------------------------------------------------------- report

def render_report(result) -> str:
    cfg, analysis = result["config"], result["analysis"]
    lines, add = [], None
    add = lines.append
    add("# Compaction: does the persona survive it?\n")
    add("Generated %s. Agent `%s`, personality judge `%s`.\n"
        % (result["finished_at"], cfg["agent_model"], cfg["judge_model"]))

    add("| setting | value |")
    add("|---|---|")
    add("| session | `%s` (%d turns in one context) |"
        % (cfg["session"], cfg["turns_per_replay"]))
    add("| context ceiling | %d tokens |" % cfg["ceiling"])
    add("| tool-result cap | %d tokens |" % cfg["tool_cap"])
    add("| repeats | %d |" % cfg["repeats"])
    add("| keep recent | %d user turns verbatim |" % cfg["keep_recent_turns"])
    add("| summary budget | %d tokens |" % cfg["summary_max_tokens"])
    add("| summariser prompt | %d tokens vs persona %d - %s |"
        % (cfg["prompt_size"]["summariser_tokens"], cfg["prompt_size"]["persona_tokens"],
           "within budget" if cfg["prompt_size"]["ok"] else "TOO LONG"))
    add("| judge rubric | `Sora/Prompts/personality.md` |")
    add("| label pool | `%s` (%d human, %d synthetic) |"
        % (cfg["label_pool"], cfg["pool"]["human"], cfg["pool"]["synthetic"]))
    add("")

    add("## Did the ceiling hold?\n")
    add("| repeat | compaction events | peak context | ceiling | held? |")
    add("|---|---|---|---|---|")
    for run in result["runs"]:
        events = sum(1 for t in run["turns"] if t["compacted_this_turn"])
        add("| %d | %d | %d | %d | %s |"
            % (run["repeat"], events, run["peak_context_tokens"], run["ceiling"],
               "yes" if run["peak_context_tokens"] <= run["ceiling"] else "**NO**"))
    add("")

    add("## Persona adherence, immediately before and after each compaction\n")
    if not result["treated"]:
        add("No compaction events fired. Lower `--ceiling` or use a session with "
            "bigger tool results.\n")
    else:
        add("| repeat | turns | before | after | change | judge's reason (after) |")
        add("|---|---|---|---|---|---|")
        for pair in result["treated"]:
            add("| %d | %s -> %s | %s | %s | %s | %s |"
                % (pair["repeat"], pair["before_turn"], pair["after_turn"],
                   pair.get("before_score"), pair.get("after_score"),
                   stats.fmt(pair.get("delta"), "%+d"), pair.get("after_reason", "")[:70]))
        add("")

    if result.get("matched"):
        add("## The same turn, with compaction and without\n")
        add("| repeat | turn | compaction ON | compaction OFF | difference |")
        add("|---|---|---|---|---|")
        for item in result["matched"]:
            add("| %d | %s | %s | %s | %s |"
                % (item["repeat"], item["turn"], item.get("compacted_score"),
                   item.get("uncompacted_score"), stats.fmt(item.get("delta"), "%+d")))
        add("")
        add("This is the measurement that answers the question. The before/after table "
            "above cannot: compaction fires exactly when the context spikes, which is "
            "exactly the tool-result turns, and tool-result turns already flatten her "
            "into list mode. Holding the turn fixed and toggling compaction leaves "
            "compaction as the only difference.\n")

    add("## Verdict\n")
    add("| | persona score (1-5) |")
    add("|---|---|")
    add("| immediately **before** compaction (5.3) | %s |" % _cell(analysis["before"]))
    add("| immediately **after** compaction (5.3) | %s |" % _cell(analysis["after"]))
    add("| before/after change | %s%s |"
        % (_cell(analysis["paired_delta"], "%+.2f"),
           "  95%% CI [%+.2f, %+.2f]" % analysis["paired_ci95"]
           if analysis["paired_ci95"] else ""))
    if analysis["matched_delta"]["n"]:
        add("| same turn, compaction **on** | %s |" % _cell(analysis["matched_compacted"]))
        add("| same turn, compaction **off** | %s |" % _cell(analysis["matched_uncompacted"]))
        add("| **turn-matched change** | **%s%s** |"
            % (_cell(analysis["matched_delta"], "%+.2f"),
               "  95%% CI [%+.2f, %+.2f]" % analysis["matched_ci95"]
               if analysis["matched_ci95"] else ""))
    add("| turn-to-turn noise **without** compaction | %s points (n=%d) |"
        % (stats.fmt(analysis["control_mean_abs_delta"]), analysis["control_n"]))
    add("")
    add("**%s** - %s.\n" % (analysis["verdict"].upper(), analysis["verdict_reason"]))
    add("Judged on: %s.\n" % analysis["primary_measure"])
    add("The noise row is what gives the others meaning: persona scores move between any "
        "two adjacent turns regardless. Compaction only matters if it moves them "
        "further than that.\n")

    add("## What compaction cost and saved\n")
    add("| | value |")
    add("|---|---|")
    add("| summariser calls | %d |" % result["cost"]["compaction_calls"])
    add("| agent + summariser | $%.4f |" % result["cost"]["agent_usd"])
    add("| judge calls | $%.4f |" % result["cost"]["judge_usd"])
    add("| **total** | **$%.4f** of a $%.2f ceiling |"
        % (result["cost"]["total_usd"], result["cost"]["budget"]["max_usd"]))
    add("")
    if result.get("example_summary"):
        add("## A compaction note, as produced\n")
        add("```\n%s\n```\n" % result["example_summary"][:1200])
    if result.get("stopped_early"):
        add("> **Stopped early:** %s\n" % result["stopped_early"])
    return "\n".join(lines) + "\n"


def _cell(summary, spec="%.2f"):
    if not summary or summary.get("mean") is None:
        return "n/a"
    if summary.get("sd") is None:
        return (spec + " (n=%d)") % (summary["mean"], summary["n"])
    return (spec + " +/- %.2f (n=%d)") % (summary["mean"], summary["sd"], summary["n"])


# ---------------------------------------------------------------------- main

def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(
        description="Prove compaction keeps Sora in character (ASSIGNMENT.md 5.3).")
    ap.add_argument("--session", nargs="+", default=[str(p) for p in DEFAULT_SESSIONS],
                    help="session fixture(s), chained into one context "
                         "(default: all three, which is what reaches 8k)")
    ap.add_argument("--profile", default="sora", choices=["sora", "baseline"])
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--ceiling", type=int, default=None,
                    help="context ceiling (default 8000; lower it to force compaction "
                         "on a session that would otherwise fit)")
    ap.add_argument("--no-control-arm", dest="control_arm", action="store_false",
                    help="skip the uncompacted replay (halves the cost, loses the "
                         "only measurement that separates compaction from turn type)")
    ap.add_argument("--margin", type=float, default=0.5,
                    help="equivalence margin in scale points (default 0.5)")
    ap.add_argument("--max-usd", type=float, default=1.00)
    ap.add_argument("--pool", default=None)
    ap.add_argument("--out", default=str(DEFAULT_REPORT))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    guard = Ledger.CostGuard(args.max_usd, "compaction-verify")
    pool = examples_mod.load(args.pool)
    persona = profiles_mod.get(args.profile).persona
    stopped = None

    runs, uncompacted_runs, treated, control = [], [], [], []
    cost_before = Ledger.totals()["usd"]
    compaction_calls = 0

    for repeat in range(args.repeats):
        print("replay %d/%d  %s (ceiling %s)"
              % (repeat + 1, args.repeats, " + ".join(runner.relpath(p) for p in args.session),
                 args.ceiling or Ledger.context_ceiling()))
        try:
            run = run_session(args.session, args.profile, repeat, args.ceiling, guard)
            events = sum(1 for t in run["turns"] if t["compacted_this_turn"])
            compaction_calls += sum(len(t["events"]) for t in run["turns"])
            print("  compaction ON : %d event(s), peak context %d tokens"
                  % (events, run["peak_context_tokens"]))
            runs.append(run)
            if args.control_arm:
                off = run_session(args.session, args.profile, repeat, args.ceiling,
                                  guard, compaction=False)
                print("  compaction OFF: peak context %d tokens (%s the ceiling)"
                      % (off["peak_context_tokens"],
                         "over" if off["peak_context_tokens"] > off["ceiling"] else "under"))
                uncompacted_runs.append(off)
        except Ledger.BudgetExceeded as exc:
            stopped = str(exc)
            break
    agent_cost = Ledger.totals()["usd"] - cost_before

    for run in runs:
        run_treated, run_control = pairs_from(run)
        try:
            treated += score_pairs(run_treated, "compaction", run, guard, pool, persona,
                                   args.quiet)
            control += score_pairs(run_control, "control", run, guard, pool, persona,
                                   args.quiet)
        except Ledger.BudgetExceeded as exc:
            stopped = stopped or str(exc)
            break

    matched = []
    by_repeat = {r["repeat"]: r for r in uncompacted_runs}
    for pair in treated:
        off = by_repeat.get(pair["repeat"])
        if not off:
            continue
        turn = pair["after_turn"]
        off_turn = next((t for t in off["turns"] if t["turn"] == turn), None)
        if not off_turn:
            continue
        matched.append({
            "turn": turn, "repeat": pair["repeat"], "session_id": pair["session_id"],
            "profile": args.profile, "user": pair["after_user"],
            "compacted_reply": pair["after_reply"],
            "compacted_score": pair.get("after_score"),
            "compacted_reason": pair.get("after_reason"),
            "uncompacted_reply": off_turn["reply"],
        })
    if matched:
        try:
            matched = score_matched(matched, guard, pool, persona, args.quiet)
        except Ledger.BudgetExceeded as exc:
            stopped = stopped or str(exc)
    judge_cost = Ledger.totals()["usd"] - cost_before - agent_cost

    from Sora.Context import max_tool_result_tokens

    reference = runs[0] if runs else None
    result = {
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": {
            "session": " + ".join(runner.relpath(p) for p in args.session),
            "turns_per_replay": len(load_turns(args.session)[0]),
            "profile": args.profile,
            "repeats": len(runs),
            "ceiling": reference["ceiling"] if reference else (
                args.ceiling or Ledger.context_ceiling()),
            "tool_cap": max_tool_result_tokens(),
            "keep_recent_turns": _compactor_default("keep_recent_turns"),
            "summary_max_tokens": _compactor_default("summary_max_tokens"),
            "prompt_size": check_prompt_size(persona),
            "agent_model": runner._model_name(),
            "judge_model": judge_mod.judge_model(),
            "label_pool": runner.relpath(examples_mod.pool_path(args.pool)),
            "pool": examples_mod.stats(pool, "personality"),
        },
        "runs": runs,
        "uncompacted_runs": uncompacted_runs,
        "treated": treated,
        "control": control,
        "matched": matched,
        "analysis": analyse(treated, control, args.margin, matched),
        "example_summary": reference.get("summary") if reference else None,
        "cost": {
            "agent_usd": round(agent_cost, 6),
            "judge_usd": round(judge_cost, 6),
            "total_usd": round(agent_cost + judge_cost, 6),
            "compaction_calls": compaction_calls,
            "budget": guard.as_dict(),
        },
        "stopped_early": stopped,
    }

    report_path = pathlib.Path(args.out)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(result), encoding="utf-8")
    report_path.with_suffix(".json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    analysis = result["analysis"]
    print()
    print("persona before compaction : %s" % _cell(analysis["before"]))
    print("persona after compaction  : %s" % _cell(analysis["after"]))
    print("before/after change       : %s%s"
          % (_cell(analysis["paired_delta"], "%+.2f"),
             "  95%% CI [%+.2f, %+.2f]" % analysis["paired_ci95"]
             if analysis["paired_ci95"] else ""))
    if analysis["matched_delta"]["n"]:
        print("same turn, compaction on  : %s" % _cell(analysis["matched_compacted"]))
        print("same turn, compaction off : %s" % _cell(analysis["matched_uncompacted"]))
        print("turn-matched change       : %s%s"
              % (_cell(analysis["matched_delta"], "%+.2f"),
                 "  95%% CI [%+.2f, %+.2f]" % analysis["matched_ci95"]
                 if analysis["matched_ci95"] else ""))
    print("no-compaction control     : %s points of turn-to-turn noise (n=%d)"
          % (stats.fmt(analysis["control_mean_abs_delta"]), analysis["control_n"]))
    print("VERDICT: %s - %s" % (analysis["verdict"].upper(), analysis["verdict_reason"]))
    print()
    print(guard.summary())
    print("report : %s" % runner.relpath(report_path))
    print("data   : %s" % runner.relpath(report_path.with_suffix(".json")))
    return 0 if analysis["verdict"] != "degraded" else 1


def _compactor_default(name):
    from Sora.Compaction import compactor

    return getattr(compactor, "DEFAULT_" + name.upper())


if __name__ == "__main__":
    raise SystemExit(main())
