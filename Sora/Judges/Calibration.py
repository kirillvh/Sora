"""Calibration: you grade some turns, the judges learn to grade like you.

    python -m Sora.Judges.Calibration                  # label 20 turns, report, benchmark
    python -m Sora.Judges.Calibration --n 8 --sessions fixtures/sessions/session_3.json
    python -m Sora.Judges.Calibration --report-only    # re-score existing labels
    python -m Sora.Judges.Calibration --seed-synthetic 4   # cold-start anchors, no human

This is the closed loop ASSIGNMENT.md 7.5 asks for, plus the half it does not:
the labels are not only compared against the judge, they *become* the judge's
few-shot examples. Label 20 turns today and every judge call from tomorrow
imitates your severity.

You score three axes per turn and write one line of rationale. Nothing is
typed as JSON - this file writes `Sora/Judges/labels/pool.jsonl` for you, one
line per labelled turn, appended as you go so a Ctrl-C keeps everything up to
that point.

Two deliberate choices in the labelling UI:

**Blind by default.** Replies from both profiles are shuffled together and the
profile is hidden, because knowing "this is the improved one" is exactly the
bias that would make the A/B meaningless. `--reveal-profile` if you want it.

**Leave-one-out scoring.** When the judge is scored against your labels, the
turn being graded is excluded from its own few-shot block. Otherwise the judge
would be shown the answer and we would measure nothing.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import re
import sys
from datetime import datetime, timezone

from Sora import Ledger
from Sora.Judges import examples as examples_mod
from Sora.Judges import judge as judge_mod
from Sora.Judges import profiles as profiles_mod
from Sora.Judges import runner, stats, templates

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_REPORT = REPO_ROOT / "out" / "benchmark" / "calibration_report.md"

AXES = templates.AXES


# ------------------------------------------------------------- the label CLI

def _scale_of(axis) -> str:
    """The '## Scale' block of a rubric, for the in-CLI '?' helper."""
    text = templates.load(axis)
    match = re.search(r"## Scale\n(.*?)(?=\n## )", text, re.S)
    body = match.group(1) if match else text
    return re.sub(r"\{\{[A-Z_]+\}\}", "", body).strip()[:1200]


def _prompt(msg):
    try:
        return input(msg).strip()
    except (EOFError, KeyboardInterrupt):
        return "q"


def _read_scores(item_no, total):
    """Returns (scores dict, rationale) or (None, reason) for skip/quit."""
    print("\n  Scores 1-5 for personality novelty initiative.")
    print("  Enter all three at once ('4 2 5'), or one at a time.")
    print("  '?<axis>' shows a rubric (e.g. '?novelty'), 's' skips, 'q' saves and stops.")
    scores = {}
    while len(scores) < len(AXES):
        pending = [a for a in AXES if a not in scores]
        raw = _prompt("  %s > " % pending[0])
        if raw in ("q", "quit"):
            return None, "quit"
        if raw in ("s", "skip"):
            return None, "skip"
        if raw.startswith("?"):
            axis = raw[1:].strip() or pending[0]
            if axis in AXES:
                print("\n" + _scale_of(axis) + "\n")
            else:
                print("  axes: %s" % ", ".join(AXES))
            continue
        parts = raw.split()
        if not parts:
            continue
        try:
            values = [int(p) for p in parts]
        except ValueError:
            print("  numbers 1-5 please")
            continue
        if any(not 1 <= v <= 5 for v in values):
            print("  numbers 1-5 please")
            continue
        for axis, value in zip(pending, values):
            scores[axis] = value
    rationale = _prompt("  one-line rationale > ")
    if rationale in ("q", "quit"):
        return None, "quit"
    return scores, rationale


def label_interactively(items, labeller, pool_path, reveal_profile=False) -> list:
    print("\n" + "=" * 78)
    print("LABELLING  %d turns. Your scores become the judges' few-shot examples."
          % len(items))
    print("=" * 78)
    written = []
    for i, item in enumerate(items, 1):
        print("\n" + "-" * 78)
        head = "[%d/%d]  %s turn %s" % (i, len(items), item["session_id"], item["turn"])
        if reveal_profile:
            head += "   profile=%s" % item["profile"]
        print(head)
        print("-" * 78)
        print("USER: %s" % item["user"])
        print("SORA: %s" % item["reply"])
        scores, rationale = _read_scores(i, len(items))
        if scores is None:
            if rationale == "quit":
                print("\n  stopping here; %d labels saved." % len(written))
                break
            print("  skipped")
            continue
        record = examples_mod.make_record(
            user=item["user"], reply=item["reply"], scores=scores, rationale=rationale,
            source="human", labeller=labeller, session_id=item["session_id"],
            turn=item["turn"], profile=item["profile"])
        examples_mod.append(record, pool_path)   # append per item: Ctrl-C is safe
        written.append(record)
    return written


# ------------------------------------------------------------ item selection

def collect_items(transcripts, n, rng, exclude_keys=()) -> list:
    """Flatten transcripts into labellable turns, balanced across profiles.

    Round-robin by profile so a 20-item sheet is 10 baseline and 10 sora rather
    than whatever the shuffle happened to produce - the whole point of showing
    both profiles is score variance, and that needs both sides present.
    """
    by_profile: dict = {}
    for transcript in transcripts:
        for turn in transcript.get("turns", []):
            if not (turn.get("reply") or "").strip():
                continue
            key = (transcript["session_id"], turn["turn"], transcript["profile"])
            if key in exclude_keys:
                continue
            by_profile.setdefault(transcript["profile"], []).append({
                "session_id": transcript["session_id"],
                "turn": turn["turn"],
                "profile": transcript["profile"],
                "user": turn["user"],
                "reply": turn["reply"],
            })
    for rows in by_profile.values():
        rng.shuffle(rows)

    picked, names = [], sorted(by_profile)
    while len(picked) < n and any(by_profile[name] for name in names):
        for name in names:
            if by_profile[name]:
                picked.append(by_profile[name].pop())
                if len(picked) >= n:
                    break
    rng.shuffle(picked)      # blind: the human should not see them profile-grouped
    return picked


# ------------------------------------------------------------------ synthetic

def seed_synthetic(session_path, count, guard, pool_path=None, quiet=False) -> list:
    """Cold-start personality anchors: same user turns, clean card vs corrupted.

    Sora/Judges/__init__.py's idea. These are labelled 5 and 1 by construction,
    not by judgement, and they are tagged `synthetic` so the agreement stats
    ignore them. They exist so a judge run before anybody has labelled anything
    still sees both ends of the scale.
    """
    session = json.loads(pathlib.Path(session_path).read_text(encoding="utf-8"))
    session_id = session.get("session_id", pathlib.Path(session_path).stem)
    turns = session.get("turns", [])[:count]
    written = []
    for profile, score, why in (("sora", 5, "generated from the intact persona card"),
                                ("corrupted", 1, "generated from a deliberately voiceless card")):
        agent = profiles_mod.build_agent(profile, cached_search=True)
        if hasattr(agent, "start_session"):
            agent.start_session("%s#synthetic#%s" % (session_id, profile))
        with Ledger.call_context(tag="calibration:synthetic", tags=["profile:%s" % profile]):
            for i, user_text in enumerate(turns, 1):
                guard.check("synthetic %s turn %d" % (profile, i))
                reply = agent.respond(user_text)
                record = examples_mod.make_record(
                    user=user_text, reply=reply, scores={"personality": score},
                    rationale=why, source="synthetic", labeller="seed_synthetic",
                    session_id=session_id, turn=i, profile=profile)
                written.append(examples_mod.append(record, pool_path))
                if not quiet:
                    print("  [%s -> personality %d] %s" % (profile, score, reply[:60]))
    return written


# ---------------------------------------------------------------- agreement

def score_labels_with_judges(labels, pool, guard, k=None, quiet=False) -> list:
    """Judge every labelled turn, leaving that turn out of its own examples."""
    out = []
    for i, label in enumerate(labels, 1):
        row = {"id": label["id"], "session_id": label.get("session_id"),
               "turn": label.get("turn"), "profile": label.get("profile"),
               "user": label["user"], "reply": label["reply"],
               "human": dict(label.get("scores") or {}),
               "human_rationale": label.get("rationale", ""),
               "judge": {}, "judge_reason": {}}
        persona = (profiles_mod.get(label["profile"]).persona
                   if label.get("profile") in profiles_mod.PROFILES else "")
        rng = random.Random("calib|%s" % label["id"])
        for axis in AXES:
            if label.get("scores", {}).get(axis) is None:
                continue
            guard.check("judge %s %s" % (axis, label["id"]))
            block, _ = examples_mod.block_for(
                axis, pool=pool, rng=rng, exclude_ids=[label["id"]],
                **({"k": k} if k is not None else {}))
            result = judge_mod.score_turn(
                axis, label["user"], label["reply"], persona=persona,
                examples_block=block, session_id=label.get("session_id", ""),
                turn=label.get("turn"), profile=label.get("profile", ""))
            row["judge"][axis] = result["score"]
            row["judge_reason"][axis] = result["reason"]
        out.append(row)
        if not quiet:
            deltas = " ".join("%s %s->%s" % (a[:4], row["human"].get(a), row["judge"].get(a))
                              for a in AXES if a in row["human"])
            print("  [%d/%d] %s" % (i, len(labels), deltas))
    return out


def analyse_agreement(rows) -> dict:
    out = {}
    for axis in AXES:
        human = [r["human"].get(axis) for r in rows]
        judge = [r["judge"].get(axis) for r in rows]
        out[axis] = stats.agreement(human, judge)
        worst = sorted(
            [r for r in rows if r["human"].get(axis) is not None
             and r["judge"].get(axis) is not None],
            key=lambda r: -abs(r["judge"][axis] - r["human"][axis]))[:3]
        out[axis]["worst_cases"] = [{
            "id": r["id"], "human": r["human"][axis], "judge": r["judge"][axis],
            "human_rationale": r["human_rationale"],
            "judge_reason": r["judge_reason"].get(axis, ""),
            "reply": (r["reply"] or "")[:220],
        } for r in worst]
    return out


# ------------------------------------------------------------------- report

def render_report(result) -> str:
    cfg = result["config"]
    lines, add = [], None
    add = lines.append
    add("# Judge calibration report\n")
    add("Generated %s. Judge `%s`, %d hand-labelled turns across %d session(s).\n"
        % (result["finished_at"], cfg["judge_model"], result["n_labels"],
           len({p.get("session_id") for p in result["pairs"]})))
    add("| input | path |")
    add("|---|---|")
    add("| human label pool | `%s` |" % cfg["label_pool"])
    for axis, path in cfg["templates"].items():
        add("| judge rubric `%s` | `%s` |" % (axis, path))
    for path in cfg["sessions"]:
        add("| sessions labelled from | `%s` |" % path)
    add("| transcripts | `out/benchmark/transcripts/` |")
    add("")

    if not result["n_labels"]:
        add("> No human labels yet. Run `python -m Sora.Judges.Calibration` and grade "
            "some turns; this report becomes meaningful at ~20.\n")
        return "\n".join(lines) + "\n"

    add("## Agreement: human vs judge\n")
    add("| axis | n | exact | within 1 | MAE | bias | kappa (quad) | r |")
    add("|---|---|---|---|---|---|---|---|")
    for axis, agr in result["agreement"].items():
        if not agr.get("n"):
            add("| %s | 0 | - | - | - | - | - | - |" % axis)
            continue
        add("| %s | %d | %.0f%% | %.0f%% | %s | %s | %s | %s |" % (
            axis, agr["n"], agr["exact_pct"], agr["within1_pct"],
            stats.fmt(agr["mae"]), stats.fmt(agr["bias"], "%+.2f"),
            stats.fmt(agr["kappa_qw"]), stats.fmt(agr["pearson_r"])))
    add("")
    add("`bias` is judge minus human: positive means the judge is more generous than "
        "you. `kappa (quad)` is chance-corrected agreement with quadratic weights - "
        "on a 1-5 scale where most replies cluster at 3-4, raw agreement flatters a "
        "judge badly and kappa does not. Rules of thumb: <0.4 poor, 0.4-0.6 moderate, "
        ">0.6 good.\n")

    add("## Where the judge and the human disagreed most\n")
    for axis, agr in result["agreement"].items():
        if not agr.get("worst_cases"):
            continue
        add("**%s**\n" % axis)
        for case in agr["worst_cases"]:
            add("- human **%s** vs judge **%s** - you: \"%s\" / judge: \"%s\"  \n  `%s`"
                % (case["human"], case["judge"], case["human_rationale"],
                   case["judge_reason"], case["reply"].replace("\n", " ")))
        add("")

    add("## Judge biases worth worrying about\n")
    add("- **Self-preference.** The judge is `%s` and the agent is `%s`. When they are "
        "the same model, the judge is grading text drawn from its own distribution and "
        "tends to like it; the fix is a second judge on a different model, and the "
        "cross-check is cheap (`SORA_JUDGE_MODEL`)."
        % (cfg["judge_model"], cfg["agent_model"]))
    add("- **Length and enthusiasm bias.** LLM judges reliably read longer, more "
        "exclamatory replies as better on any positive-sounding axis. Both the novelty "
        "and personality rubrics tell the judge in as many words that length and "
        "enthusiasm are not the signal, which mitigates it without removing it - the "
        "`bias` column above is where it shows up first.")
    add("- **Few-shot anchoring.** The examples this loop feeds the judge are also its "
        "leniency prior: label a run of generous 5s and the judge becomes generous. "
        "That is the intended mechanism and its risk at the same time, which is why the "
        "examples are drawn spread across the score range rather than at random, and "
        "why the label pool is append-only and auditable.\n")

    cost = result["cost"]
    add("## Cost\n")
    add("- transcripts + judging this run: **$%.4f** of a $%.2f ceiling\n"
        % (cost["total_usd"], cost["budget"]["max_usd"]))
    if result.get("stopped_early"):
        add("> **Stopped early:** %s\n" % result["stopped_early"])
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------- main

def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="Hand-label turns; calibrate the judges.")
    ap.add_argument("--n", type=int, default=20, help="turns to label (default 20)")
    ap.add_argument("--sessions", nargs="*", default=None)
    ap.add_argument("--include-flood", action="store_true")
    ap.add_argument("--profiles", nargs="+", default=["baseline", "sora"])
    ap.add_argument("--labeller", default="", help="who is labelling (goes in the pool)")
    ap.add_argument("--pool", default=None)
    ap.add_argument("--reveal-profile", action="store_true",
                    help="show which profile produced each reply (default: blind)")
    ap.add_argument("--report-only", action="store_true",
                    help="skip labelling; re-score the existing human labels")
    ap.add_argument("--seed-synthetic", type=int, default=0, metavar="N",
                    help="generate N synthetic personality anchors and exit")
    ap.add_argument("--examples", type=int, default=None)
    ap.add_argument("--max-usd", type=float, default=1.00)
    ap.add_argument("--seed", default="0")
    ap.add_argument("--out", default=str(DEFAULT_REPORT))
    ap.add_argument("--no-benchmark", action="store_true",
                    help="do not run the benchmark afterwards")
    ap.add_argument("--repeats", type=int, default=3, help="repeats for the benchmark leg")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    guard = Ledger.CostGuard(args.max_usd, "calibration")
    sessions = ([pathlib.Path(p) for p in args.sessions] if args.sessions
                else runner.discover_sessions(include_flood=args.include_flood))
    rng = random.Random(args.seed)
    stopped = None

    if args.seed_synthetic:
        print("seeding %d synthetic personality anchors per side from %s"
              % (args.seed_synthetic, runner.relpath(sessions[0])))
        try:
            written = seed_synthetic(sessions[0], args.seed_synthetic, guard,
                                     args.pool, quiet=args.quiet)
        except Ledger.BudgetExceeded as exc:
            written, stopped = [], str(exc)
        print("\n%d anchors written to %s"
              % (len(written), runner.relpath(examples_mod.pool_path(args.pool))))
        print(guard.summary())
        return 0

    pool_before = examples_mod.load(args.pool)
    labels = examples_mod.human_labels(pool_before)

    if not args.report_only:
        print("preparing transcripts to label from (cached where possible)")
        try:
            transcripts = runner.generate_matrix(args.profiles, sessions, 1, guard=guard,
                                                 quiet=args.quiet)
        except Ledger.BudgetExceeded as exc:
            transcripts, stopped = [], str(exc)
        already = {(r.get("session_id"), r.get("turn"), r.get("profile")) for r in labels}
        items = collect_items(transcripts, args.n, rng, exclude_keys=already)
        if not items:
            print("nothing left to label in these sessions (all already labelled)")
        else:
            new_labels = label_interactively(items, args.labeller,
                                             args.pool, args.reveal_profile)
            labels = examples_mod.human_labels(examples_mod.load(args.pool))
            print("\n%d new label(s); pool now holds %d human label(s)."
                  % (len(new_labels), len(labels)))

    pool = examples_mod.load(args.pool)
    rows = []
    if labels:
        print("\nscoring your %d labelled turns with the judges (leave-one-out)" % len(labels))
        try:
            rows = score_labels_with_judges(labels, pool, guard, k=args.examples,
                                            quiet=args.quiet)
        except Ledger.BudgetExceeded as exc:
            stopped = stopped or str(exc)

    result = {
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": {
            "judge_model": judge_mod.judge_model(),
            "agent_model": runner._model_name(),
            "templates": templates.relpaths(),
            # In --report-only there was no session selection: name what the
            # labels actually came from rather than the discovery default.
            "sessions": (sorted({str(r.get("session_id")) for r in rows})
                         if args.report_only and rows
                         else [runner.relpath(p) for p in sessions]),
            "label_pool": runner.relpath(examples_mod.pool_path(args.pool)),
            "profiles": args.profiles,
        },
        "n_labels": len(rows),
        "pairs": rows,
        "agreement": analyse_agreement(rows) if rows else {},
        "cost": {"total_usd": round(guard.spent(), 6), "budget": guard.as_dict()},
        "stopped_early": stopped,
    }

    report_path = pathlib.Path(args.out)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(result), encoding="utf-8")
    report_path.with_suffix(".json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print(guard.summary())
    print("calibration report : %s" % runner.relpath(report_path))
    print("calibration data   : %s" % runner.relpath(report_path.with_suffix(".json")))
    print("label pool         : %s" % result["config"]["label_pool"])

    if args.no_benchmark:
        print("\nskipping the benchmark leg (--no-benchmark). To run it:")
        print("  python -m Sora.Judges.Benchmark --repeats %d --max-usd %.2f"
              % (args.repeats, guard.remaining()))
        return 0
    if guard.remaining() <= 0:
        print("\nbudget exhausted; not starting the benchmark leg.")
        return 0

    print("\n" + "=" * 78)
    print("proceeding to the benchmark with the calibrated judges ($%.4f of the "
          "$%.2f ceiling left)" % (guard.remaining(), args.max_usd))
    print("=" * 78)
    from Sora.Judges import Benchmark

    return Benchmark.main([
        "--profiles", *args.profiles,
        "--sessions", *[str(p) for p in sessions],
        "--repeats", str(args.repeats),
        "--max-usd", "%.4f" % guard.remaining(),
        "--seed", str(args.seed),
    ] + (["--pool", args.pool] if args.pool else []) + (["--quiet"] if args.quiet else []))


if __name__ == "__main__":
    raise SystemExit(main())
