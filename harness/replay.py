"""Replay a scripted session fixture against an agent.

Usage (from the repo root):

    python -m harness.replay fixtures/sessions/session_1.json
    python -m harness.replay fixtures/sessions/session_2.json \
        --agent my_agent.agent:MySora --cached --out out/session_2.jsonl

Agent contract:
    - a class with  respond(user_text: str) -> str          (required)
    - __init__ may accept cached_search=<bool>              (optional)
    - start_session(session_id) / end_session(session_id)   (optional hooks —
      the natural place to load and persist memory between sessions)

Sessions must be replayed IN ORDER (session_1 -> 2 -> 3) for the memory
tests to mean anything. Each replay is a separate process: whatever your
agent doesn't persist to disk is gone.
"""
import argparse
import importlib
import inspect
import json
import pathlib
import sys


def load_agent(spec: str, cached: bool):
    mod_name, _, cls_name = spec.partition(":")
    cls = getattr(importlib.import_module(mod_name), cls_name)
    # Pass cached_search only if the agent really accepts it. Catching TypeError
    # here instead would also swallow a TypeError raised *inside* the agent's own
    # __init__ and silently fall back to LIVE search mid-eval.
    params = inspect.signature(cls).parameters
    accepts_cached = "cached_search" in params or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
    )
    return cls(cached_search=cached) if accepts_cached else cls()


def main():
    for _stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp932/cp950
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Replay a session fixture.")
    parser.add_argument("session", help="Path to a fixtures/sessions/*.json file")
    parser.add_argument(
        "--agent",
        default="baseline.agent:SoraAgent",
        help="module.path:ClassName of the agent to drive (default: baseline)",
    )
    parser.add_argument(
        "--cached", action="store_true", default=True,
        help="Use frozen search fixtures (default: on for replays)",
    )
    parser.add_argument("--live", dest="cached", action="store_false",
                        help="Use live web search instead of fixtures")
    parser.add_argument("--out", help="Write the transcript as jsonl to this path")
    args = parser.parse_args()

    session = json.loads(pathlib.Path(args.session).read_text(encoding="utf-8"))
    session_id = session.get("session_id", pathlib.Path(args.session).stem)
    agent = load_agent(args.agent, cached=args.cached)

    if hasattr(agent, "start_session"):
        agent.start_session(session_id)

    out_path = pathlib.Path(args.out) if args.out else None
    out_f = None
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_f = out_path.open("w", encoding="utf-8")

    turns_done = 0
    try:
        for i, user_text in enumerate(session["turns"], 1):
            print(f"\n[{session_id} | turn {i}]")
            print("you  >", user_text)
            reply = agent.respond(user_text)
            print("sora >", reply)
            if out_f:
                # Flushed per turn on purpose: a crash mid-session (the flood
                # turn is designed to provoke one) keeps the turns that ran.
                out_f.write(json.dumps(
                    {"turn": i, "user": user_text, "assistant": reply},
                    ensure_ascii=False) + "\n")
                out_f.flush()
            turns_done = i

        if hasattr(agent, "end_session"):
            agent.end_session(session_id)
    finally:
        if out_f:
            out_f.close()
            print(f"\n{turns_done}/{len(session['turns'])} turns written to {out_path}")


if __name__ == "__main__":
    main()
