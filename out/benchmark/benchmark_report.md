# Sora benchmark - persona profile A/B

Generated 2026-08-25T04:54:32+00:00. Agent `openai/gpt-4o-mini`, judge `openai/gpt-4o-mini`.

## What produced these numbers

| input | path |
|---|---|
| profile `baseline` | `baseline/persona.md` |
| profile `sora` | `Sora/Prompts/persona.md` |
| judge rubric `personality` | `Sora/Prompts/personality.md` |
| judge rubric `novelty` | `Sora/Prompts/novelty.md` |
| judge rubric `initiative` | `Sora/Prompts/initiative.md` |
| session | `fixtures/sessions/session_3.json` |
| human label pool | `Sora/Judges/labels/pool.jsonl` |
| transcripts | `out/benchmark/transcripts/` |

Repeats per profile x session: **3**. Seed: `0`. Few-shot examples per judge call: up to **6**, drawn spread across the score range.
Tool results capped at **8000 tokens** before entering context.

Label pool at run time - personality: 0 human / 6 synthetic, novelty: 0 human / 0 synthetic, initiative: 0 human / 0 synthetic.

> **The pool holds no human labels.** These judges are running on their rubrics alone, which is a valid cold-start but is NOT calibrated. Run `python -m Sora.Judges.Calibration` before quoting these numbers as measured agreement with human taste.

## Results by profile

| profile | personality | novelty | initiative | initiative rate | question rate | runs |
|---|---|---|---|---|---|---|
| `baseline` | 4.67 +/- 0.07 (n=3) | 1.75 +/- 0.12 (n=3) | 3.88 +/- 0.25 (n=3) | 92% +/- 7 (n=3) | 67% +/- 36 (n=3) | 3 |
| `sora` | 4.67 +/- 0.19 (n=3) | 1.71 +/- 0.07 (n=3) | 3.71 +/- 0.07 (n=3) | 88% +/- 0 (n=3) | 88% +/- 0 (n=3) | 3 |

Scores are 1-5 per turn, averaged within a run, then averaged across runs; `+/-` is the sample standard deviation across runs, which is the run-to-run nondeterminism of the whole pipeline (agent and judge together).

Initiative rate is the share of turns scoring >= 4, against a target of 10% - for initiative, closer to target is better, not higher. Question rate is the share of replies containing a `?`: a deliberately dumb cross-check on the initiative judge.

## Difference: `sora` minus `baseline`

| metric | delta | 95% CI | resolved at this n? |
|---|---|---|---|
| personality | +0.00 | [-0.51, 0.51] | no |
| novelty | -0.04 | [-0.31, 0.22] | no |
| initiative | -0.17 | [-0.81, 0.48] | no |
| question_rate | +0.21 | [-0.69, 1.10] | no |
| initiative_rate | -0.04 | [-0.22, 0.14] | no |

A CI spanning zero means 3 repeats could not separate the profiles on that axis. Widen `--repeats` before concluding anything from it.

## By session

| session | profile | personality | novelty | initiative |
|---|---|---|---|---|
| `session_3` | `baseline` | 4.67 +/- 0.07 (n=3) | 1.75 +/- 0.12 (n=3) | 3.88 +/- 0.25 (n=3) |
| `session_3` | `sora` | 4.67 +/- 0.19 (n=3) | 1.71 +/- 0.07 (n=3) | 3.71 +/- 0.07 (n=3) |

## Cost

| | USD |
|---|---|
| agent turns | $0.0000 |
| judge calls | $0.0176 |
| **total this run** | **$0.0176** |
| ceiling | $0.50 |

Full per-call detail is in `out/trace.jsonl`; `python -m Sora.Ledger.report_stats --by category` splits agent from judge.

## Reading these numbers honestly

- **n is small.** 3 repeats per cell. Treat the sd as the real signal and the mean as provisional.
- **The judge shares a model with the agent** unless `SORA_JUDGE_MODEL` says otherwise, so self-preference bias is live: `openai/gpt-4o-mini` judging `openai/gpt-4o-mini`.
- **Novelty and personality are correlated in practice** even though they are scored in separate calls: a reply written in a strong voice tends to read as more opinionated.
- **Initiative is a rate with a target**, so 'improvement' means moving toward 10%, and a profile that asks a question every single turn is worse, not better.
