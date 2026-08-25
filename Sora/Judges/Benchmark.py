"""Benchmark: two persona profiles, three axes, repeated runs, one report.

    python -m Sora.Judges.Benchmark
    python -m Sora.Judges.Benchmark --sessions fixtures/sessions/session_3.json --repeats 3
    python -m Sora.Judges.Benchmark --profiles baseline sora --max-usd 1.00

ASSIGNMENT.md 7.2 wants a before and an after. This produces both in one run:
`baseline` is the shipped persona plus its "answer thoroughly" clamp, `sora` is
ours. Everything else - loop, tools, model, judge, rubric - is held constant,
so the only free variable is the profile.

The agent is nondeterministic, so every profile x session is run `--repeats`
times and every number is reported as mean +/- sd over those runs, with a
confidence interval on the difference between profiles. With the default 3
repeats that interval is wide enough that small differences will not clear it.
That is the correct outcome, not a bug: it says "3 runs cannot resolve this",
and the fix is more repeats and a bigger budget, not a narrower interval.

Cost is bounded by `--max-usd` (default $1.00). The guard is checked before
each session and before each judge call, so a runaway matrix stops and still
writes a partial report rather than eating the key.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import sys
from datetime import datetime, timezone

from Sora import Ledger
from Sora.Judges import examples as examples_mod
from Sora.Judges import judge as judge_mod
from Sora.Judges import profiles as profiles_mod
from Sora.Judges import runner, stats, templates

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_REPORT = REPO_ROOT / "out" / "benchmark" / "benchmark_report.md"


def run(profile_names, session_paths, repeats, axes, guard, *, seed=0, k_examples=None,
        regenerate=False, pool_path=None, quiet=False) -> dict:
    pool = examples_mod.load(pool_path)
    started = datetime.now(timezone.utc)

    print("generating transcripts (%d profiles x %d sessions x %d repeats)"
          % (len(profile_names), len(session_paths), repeats))
    cost_before = Ledger.totals()["usd"]
    transcripts = runner.generate_matrix(profile_names, session_paths, repeats,
                                         guard=guard, regenerate=regenerate, quiet=quiet)
    agent_cost = Ledger.totals()["usd"] - cost_before

    print("judging %d transcripts on %d axes" % (len(transcripts), len(axes)))
    judged, stopped = [], None
    for transcript in transcripts:
        # Seeded per run: examples still rotate turn to turn (that is what makes
        # them hard to game) but the whole benchmark replays identically.
        rng = random.Random("%s|%s|%s|%s" % (seed, transcript["profile"],
                                             transcript["session_id"], transcript["repeat"]))
        try:
            result = judge_mod.score_transcript(
                transcript, axes=axes, pool=pool, rng=rng, k=k_examples, guard=guard,
                persona=profiles_mod.get(transcript["profile"]).persona
                if transcript["profile"] in profiles_mod.PROFILES else "")
        except Ledger.BudgetExceeded as exc:
            stopped = str(exc)
            break
        judged.append(result)
        if not quiet:
            summary = "  ".join(
                "%s %s" % (a[:4], stats.fmt(result["metrics"].get("%s_mean" % a)))
                for a in axes)
            print("  %-9s %-12s r%d   %s" % (result["profile"], result["session_id"],
                                             result["repeat"], summary))
    judge_cost = Ledger.totals()["usd"] - cost_before - agent_cost

    return {
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": {
            "profiles": profile_names,
            "sessions": [runner.relpath(p) for p in session_paths],
            "repeats": repeats,
            "axes": list(axes),
            "seed": seed,
            "agent_model": runner._model_name(),
            "judge_model": judge_mod.judge_model(),
            "templates": templates.relpaths(),
            "persona_files": {name: profiles_mod.get(name).relpath()
                              for name in profile_names if name in profiles_mod.PROFILES},
            "label_pool": runner.relpath(examples_mod.pool_path(pool_path)),
            "pool_stats": {axis: examples_mod.stats(pool, axis) for axis in axes},
            "few_shot_examples_max": k_examples or examples_mod.DEFAULT_MAX_EXAMPLES,
            "tool_result_cap_tokens": _tool_cap(),
        },
        "runs": judged,
        "analysis": analyse(judged, profile_names, axes),
        "cost": {
            "agent_usd": round(agent_cost, 6),
            "judge_usd": round(judge_cost, 6),
            "total_usd": round(agent_cost + judge_cost, 6),
            "budget": guard.as_dict(),
        },
        "stopped_early": stopped or guard.stopped_at,
    }


def _tool_cap():
    from Sora.Context import max_tool_result_tokens

    return max_tool_result_tokens()


METRIC_KEYS = ("question_rate", "initiative_rate")


def analyse(judged, profile_names, axes) -> dict:
    """Per-profile spread across runs, and the profile-to-profile difference."""
    per_profile: dict = {}
    for name in profile_names:
        runs = [r for r in judged if r["profile"] == name]
        entry = {"runs": len(runs), "sessions": sorted({r["session_id"] for r in runs})}
        for axis in axes:
            entry[axis] = stats.summarise([r["metrics"].get("%s_mean" % axis) for r in runs])
        for key in METRIC_KEYS:
            entry[key] = stats.summarise([r["metrics"].get(key) for r in runs])
        entry["unparsed_judgements"] = sum(
            r["metrics"].get("%s_unparsed" % axis, 0) for r in runs for axis in axes)
        per_profile[name] = entry

    deltas = {}
    if len(profile_names) >= 2:
        a, b = profile_names[0], profile_names[1]
        runs_a = [r for r in judged if r["profile"] == a]
        runs_b = [r for r in judged if r["profile"] == b]
        for axis in list(axes) + list(METRIC_KEYS):
            key = "%s_mean" % axis if axis in axes else axis
            deltas[axis] = stats.difference(
                [r["metrics"].get(key) for r in runs_a],
                [r["metrics"].get(key) for r in runs_b])
        deltas["_pair"] = [a, b]

    per_session: dict = {}
    for result in judged:
        bucket = per_session.setdefault(result["session_id"], {})
        entry = bucket.setdefault(result["profile"], {axis: [] for axis in axes})
        for axis in axes:
            entry[axis].append(result["metrics"].get("%s_mean" % axis))
    for session, profiles_in in per_session.items():
        for name, axis_map in profiles_in.items():
            per_session[session][name] = {axis: stats.summarise(vals)
                                          for axis, vals in axis_map.items()}

    return {"per_profile": per_profile, "deltas": deltas, "per_session": per_session}


# -------------------------------------------------------------------- report

def _cell(summary):
    if not summary or summary.get("mean") is None:
        return "n/a"
    if summary.get("sd") is None:
        return "%.2f (n=%d)" % (summary["mean"], summary["n"])
    return "%.2f +/- %.2f (n=%d)" % (summary["mean"], summary["sd"], summary["n"])


def _pct_cell(summary):
    if not summary or summary.get("mean") is None:
        return "n/a"
    sd = "" if summary.get("sd") is None else " +/- %.0f" % (100 * summary["sd"])
    return "%.0f%%%s (n=%d)" % (100 * summary["mean"], sd, summary["n"])


def render_report(result) -> str:
    cfg, analysis = result["config"], result["analysis"]
    axes = cfg["axes"]
    lines = []
    add = lines.append

    add("# Sora benchmark - persona profile A/B\n")
    add("Generated %s. Agent `%s`, judge `%s`.\n"
        % (result["finished_at"], cfg["agent_model"], cfg["judge_model"]))

    add("## What produced these numbers\n")
    add("| input | path |")
    add("|---|---|")
    for name, path in cfg["persona_files"].items():
        add("| profile `%s` | `%s` |" % (name, path))
    for axis, path in cfg["templates"].items():
        if axis in axes:
            add("| judge rubric `%s` | `%s` |" % (axis, path))
    for path in cfg["sessions"]:
        add("| session | `%s` |" % path)
    add("| human label pool | `%s` |" % cfg["label_pool"])
    add("| transcripts | `out/benchmark/transcripts/` |")
    add("")
    add("Repeats per profile x session: **%d**. Seed: `%s`. Few-shot examples per "
        "judge call: up to **%d**, drawn spread across the score range."
        % (cfg["repeats"], cfg["seed"], cfg["few_shot_examples_max"]))
    add("Tool results capped at **%s tokens** before entering context.\n" % cfg["tool_result_cap_tokens"])

    pool_line = ", ".join("%s: %d human / %d synthetic"
                          % (axis, s["human"], s["synthetic"])
                          for axis, s in cfg["pool_stats"].items())
    add("Label pool at run time - %s.\n" % (pool_line or "empty"))
    if all(s["human"] == 0 for s in cfg["pool_stats"].values()):
        add("> **The pool holds no human labels.** These judges are running on their "
            "rubrics alone, which is a valid cold-start but is NOT calibrated. Run "
            "`python -m Sora.Judges.Calibration` before quoting these numbers as "
            "measured agreement with human taste.\n")

    add("## Results by profile\n")
    header = "| profile | " + " | ".join(axes) + " | initiative rate | question rate | runs |"
    add(header)
    add("|" + "---|" * (len(axes) + 4))
    for name, entry in analysis["per_profile"].items():
        row = ["`%s`" % name] + [_cell(entry.get(axis)) for axis in axes]
        row.append(_pct_cell(entry.get("initiative_rate")))
        row.append(_pct_cell(entry.get("question_rate")))
        row.append(str(entry["runs"]))
        add("| " + " | ".join(row) + " |")
    add("")
    add("Scores are 1-5 per turn, averaged within a run, then averaged across runs; "
        "`+/-` is the sample standard deviation across runs, which is the "
        "run-to-run nondeterminism of the whole pipeline (agent and judge together).\n")
    add("Initiative rate is the share of turns scoring >= %d, against a target of "
        "%.0f%% - for initiative, closer to target is better, not higher. Question "
        "rate is the share of replies containing a `?`: a deliberately dumb "
        "cross-check on the initiative judge.\n"
        % (judge_mod.INITIATIVE_THRESHOLD, 100 * judge_mod.INITIATIVE_TARGET_RATE))

    if analysis["deltas"].get("_pair"):
        a, b = analysis["deltas"]["_pair"]
        add("## Difference: `%s` minus `%s`\n" % (b, a))
        add("| metric | delta | 95% CI | resolved at this n? |")
        add("|---|---|---|---|")
        for axis, diff in analysis["deltas"].items():
            if axis == "_pair":
                continue
            ci = ("[%.2f, %.2f]" % diff["ci95"]) if diff.get("ci95") else "n/a"
            verdict = {True: "yes", False: "no", None: "n/a"}[diff.get("significant")]
            add("| %s | %s | %s | %s |" % (axis, stats.fmt(diff.get("delta"), "%+.2f"),
                                           ci, verdict))
        add("")
        add("A CI spanning zero means %d repeats could not separate the profiles on "
            "that axis. Widen `--repeats` before concluding anything from it.\n"
            % cfg["repeats"])

    add("## By session\n")
    add("| session | profile | " + " | ".join(axes) + " |")
    add("|" + "---|" * (len(axes) + 2))
    for session in sorted(analysis["per_session"]):
        for name in sorted(analysis["per_session"][session]):
            entry = analysis["per_session"][session][name]
            add("| `%s` | `%s` | %s |" % (session, name,
                                          " | ".join(_cell(entry.get(a)) for a in axes)))
    add("")

    cost = result["cost"]
    add("## Cost\n")
    add("| | USD |")
    add("|---|---|")
    add("| agent turns | $%.4f |" % cost["agent_usd"])
    add("| judge calls | $%.4f |" % cost["judge_usd"])
    add("| **total this run** | **$%.4f** |" % cost["total_usd"])
    add("| ceiling | $%.2f |" % cost["budget"]["max_usd"])
    add("")
    add("Full per-call detail is in `out/trace.jsonl`; "
        "`python -m Sora.Ledger.report_stats --by category` splits agent from judge.\n")
    if result.get("stopped_early"):
        add("> **Stopped early:** %s. The numbers above are partial.\n" % result["stopped_early"])

    add("## Reading these numbers honestly\n")
    add("- **n is small.** %d repeats per cell. Treat the sd as the real signal and "
        "the mean as provisional." % cfg["repeats"])
    add("- **The judge shares a model with the agent** unless `SORA_JUDGE_MODEL` says "
        "otherwise, so self-preference bias is live: `%s` judging `%s`."
        % (cfg["judge_model"], cfg["agent_model"]))
    add("- **Novelty and personality are correlated in practice** even though they are "
        "scored in separate calls: a reply written in a strong voice tends to read as "
        "more opinionated.")
    add("- **Initiative is a rate with a target**, so 'improvement' means moving toward "
        "%.0f%%, and a profile that asks a question every single turn is worse, not "
        "better." % (100 * judge_mod.INITIATIVE_TARGET_RATE))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------- main

def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="A/B two persona profiles with LLM judges.")
    ap.add_argument("--profiles", nargs="+", default=["baseline", "sora"],
                    help="profiles to compare (default: baseline sora)")
    ap.add_argument("--sessions", nargs="*", default=None,
                    help="session fixtures (default: fixtures/sessions + Sora/fixtures/sessions, "
                         "minus the tool-flood session)")
    ap.add_argument("--include-flood", action="store_true",
                    help="also run session_2, the 28k-token tool-flood case")
    ap.add_argument("--repeats", type=int, default=3, help="runs per profile x session")
    ap.add_argument("--axes", nargs="+", default=list(templates.AXES))
    ap.add_argument("--max-usd", type=float, default=1.00, help="hard spend ceiling")
    ap.add_argument("--examples", type=int, default=None, help="few-shot examples per judge call")
    ap.add_argument("--seed", default="0")
    ap.add_argument("--regenerate", action="store_true", help="ignore cached transcripts")
    ap.add_argument("--pool", default=None, help="label pool path")
    ap.add_argument("--out", default=str(DEFAULT_REPORT))
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    sessions = ([pathlib.Path(p) for p in args.sessions] if args.sessions
                else runner.discover_sessions(include_flood=args.include_flood))
    if not sessions:
        raise SystemExit("no session fixtures found")
    for path in sessions:
        if not path.exists():
            raise SystemExit("no such session fixture: %s" % path)

    guard = Ledger.CostGuard(args.max_usd, "benchmark")
    result = run(args.profiles, sessions, args.repeats, args.axes, guard,
                 seed=args.seed, k_examples=args.examples, regenerate=args.regenerate,
                 pool_path=args.pool, quiet=args.quiet)

    report_path = pathlib.Path(args.out)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(result), encoding="utf-8")
    json_path = report_path.with_suffix(".json")
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print(guard.summary())
    print("report : %s" % runner.relpath(report_path))
    print("data   : %s" % runner.relpath(json_path))
    print("inputs : %s | %s | pool %s"
          % (", ".join(result["config"]["sessions"]),
             ", ".join("%s=%s" % kv for kv in result["config"]["persona_files"].items()),
             result["config"]["label_pool"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
