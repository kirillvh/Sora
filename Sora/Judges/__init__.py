'''
Judge/Benchmarking System
=========================

Before improving anything, build the thing that says whether it improved.
Otherwise we cannot tell progress from a good day, and we cannot report either
one to stakeholders.

Human judgement is the ground truth; it is also the scarce resource. So the
human labels do double duty: they are the yardstick the LLM judges are
measured against, AND the few-shot examples the judges imitate. Every turn a
human grades makes the automated judge slightly more like the human.

    python -m Sora.Judges.Calibration      # you label; judges get calibrated; benchmark runs
    python -m Sora.Judges.Benchmark        # A/B the two persona profiles

Three axes (ASSIGNMENT.md 7.1), one rubric file each, all editable without
touching Python:

  personality  Sora/Prompts/personality.md  is this her voice, first person,
                                            or third-person assistant prose?
  novelty      Sora/Prompts/novelty.md      did the reply bring something the
                                            user did not already have?
  initiative   Sora/Prompts/initiative.md   how often does she ask or propose?
                                            A RATE with a target (~10%), not a
                                            thing to maximise.

Each rubric takes a `{{EXAMPLES}}` placeholder and must still render when the
pool is empty - a judge that only works after somebody has labelled 20 turns
is a judge nobody ever runs.

Defeating the gaming problem
----------------------------
A fixed few-shot block is a target: an optimiser pointed at the judge learns to
imitate those specific replies and scores well without being more fun. So the
examples rotate per judge call, drawn from the human pool spread across the
score range (examples.py) rather than sampled uniformly - a pool that is 70%
fours would otherwise teach the judge that everything is a four.

Two profiles, three axes, repeated runs
---------------------------------------
Benchmark.py compares `baseline/persona.md` (plus its "answer thoroughly"
clamp) against `Sora/Prompts/persona.md` over the session fixtures, N repeats
each, and reports mean +/- sd with a confidence interval on the difference,
because the agent is nondeterministic and one run is a sample, not a
measurement. Everything is bounded by a cost ceiling (`--max-usd`, default $1)
enforced through the Ledger.

`fixtures/sessions/session_2.json` is excluded by default: it is the 28k-token
tool-flood case, it costs 8x the others, and the judges never see tool output
anyway. `--include-flood` overrides. Extra scenarios written for these axes
live in `Sora/fixtures/sessions/`.

Where things are
----------------
  profiles.py   the two persona profiles (and a corrupted control)
  templates.py  rubric loading and placeholder rendering
  examples.py   the human label pool, the spread sampler, the few-shot block
  judge.py      one axis, one turn, one call - independent by construction
  runner.py     transcript generation, cached so human and judge grade the
                same replies
  stats.py      spread, Welch intervals, quadratic weighted kappa
  Calibration.py  the labelling CLI, agreement report, then the benchmark
  Benchmark.py    the A/B and its report

Reports land in `out/benchmark/` and name every input path they used.
'''
from .judge import AXES, score_transcript, score_turn
from .profiles import PROFILES, build_agent, system_prompt_for

__all__ = [
    "AXES",
    "PROFILES",
    "build_agent",
    "score_transcript",
    "score_turn",
    "system_prompt_for",
]
