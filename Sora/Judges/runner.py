"""Generate transcripts: one profile, one session fixture, one repeat.

Transcripts are cached on disk and reused by default. That is not a
performance nicety - it is what lets Calibration.py hand a human the *same*
replies the judge scored. Agreement between a human and a judge means nothing
if they were looking at different outputs, and the agent is nondeterministic,
so re-running would give them different outputs.

`fixtures/sessions/session_2.json` is excluded by default. It is the tool-flood
stress case (one search fixture is 28,326 tokens, and the baseline resends it
every subsequent turn: $0.026 for that session alone against $0.003 for the
others). It exercises the context budget, not the personality, and the judges
never see tool output anyway - they grade the user turn and Sora's reply. Pass
--include-flood if you want it in the numbers.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from datetime import datetime, timezone

from Sora import Ledger
from Sora.Judges import profiles as profiles_mod

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
FIXTURE_DIRS = (REPO_ROOT / "fixtures" / "sessions", REPO_ROOT / "Sora" / "fixtures" / "sessions")
DEFAULT_OUT_DIR = REPO_ROOT / "out" / "benchmark" / "transcripts"

# Sessions whose cost is dominated by a tool-result flood rather than by
# conversation. Skipped unless asked for; see the module docstring.
FLOOD_SESSIONS = {"session_2"}


def discover_sessions(include_flood: bool = False) -> list:
    found = []
    for directory in FIXTURE_DIRS:
        if directory.is_dir():
            found.extend(sorted(directory.glob("*.json")))
    if not include_flood:
        found = [p for p in found if p.stem not in FLOOD_SESSIONS]
    return found


def session_id_of(path) -> str:
    path = pathlib.Path(path)
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("session_id", path.stem)
    except Exception:
        return path.stem


def relpath(path) -> str:
    path = pathlib.Path(path).resolve()
    try:
        return str(path.relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def transcript_path(profile, session_path, repeat, out_dir=None) -> pathlib.Path:
    out_dir = pathlib.Path(out_dir or DEFAULT_OUT_DIR)
    return out_dir / ("%s__%s__r%d.json" % (profile, pathlib.Path(session_path).stem, repeat))


def generate(profile, session_path, repeat=0, *, cached_search=True, guard=None,
             out_dir=None, regenerate=False, quiet=False) -> dict:
    """Run one session through one profile. Returns the transcript dict."""
    path = transcript_path(profile, session_path, repeat, out_dir)
    if path.exists() and not regenerate:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["reused"] = True
        return data

    session = json.loads(pathlib.Path(session_path).read_text(encoding="utf-8"))
    session_id = session.get("session_id", pathlib.Path(session_path).stem)
    system_prompt = profiles_mod.system_prompt_for(profile)

    agent = profiles_mod.build_agent(profile, cached_search=cached_search)
    label = "%s#%s#r%d" % (session_id, profile, repeat)
    if hasattr(agent, "start_session"):
        agent.start_session(label)

    before = Ledger.totals()
    turns = []
    stopped = None
    with Ledger.call_context(tag="bench:%s" % profile,
                             tags=["profile:%s" % profile, "repeat:%d" % repeat]):
        for i, user_text in enumerate(session.get("turns", []), 1):
            if guard is not None:
                try:
                    guard.check("%s turn %d" % (label, i))
                except Ledger.BudgetExceeded as exc:
                    stopped = str(exc)
                    break
            reply = agent.respond(user_text)
            turns.append({"turn": i, "user": user_text, "reply": reply})
            if not quiet:
                print("    turn %-2d %s" % (i, (reply or "")[:70].replace("\n", " ")))
    after = Ledger.totals()

    transcript = {
        "profile": profile,
        "session_id": session_id,
        "session_path": relpath(session_path),
        "repeat": repeat,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": _model_name(),
        "system_prompt_sha256": hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:16],
        "cost_usd": round(after["usd"] - before["usd"], 6),
        "llm_calls": after["calls"] - before["calls"],
        "prompt_tokens": after["prompt_tokens"] - before["prompt_tokens"],
        "completion_tokens": after["completion_tokens"] - before["completion_tokens"],
        "turns": turns,
        "stopped_early": stopped,
        "reused": False,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")
    transcript["path"] = relpath(path)
    return transcript


def _model_name():
    from llm.client import model_name

    return model_name()


def generate_matrix(profile_names, session_paths, repeats=3, *, guard=None,
                    regenerate=False, out_dir=None, quiet=False) -> list:
    """profiles x sessions x repeats, in an order that degrades gracefully.

    Repeat-major: every profile and session gets its first run before any of
    them gets a second. If the cost ceiling stops us early we are left with a
    complete but noisy comparison rather than a precise measurement of the
    first profile and nothing at all for the second.
    """
    out = []
    for repeat in range(repeats):
        for session_path in session_paths:
            for profile in profile_names:
                if not quiet:
                    print("  [%s] %s repeat %d" % (profile, relpath(session_path), repeat))
                transcript = generate(profile, session_path, repeat, guard=guard,
                                      regenerate=regenerate, out_dir=out_dir, quiet=quiet)
                out.append(transcript)
                if transcript.get("stopped_early"):
                    return out
    return out
