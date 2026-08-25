# Sora benchmark - persona profile A/B

Generated 2026-08-25T05:44:17+00:00. Agent `openai/gpt-4o-mini`, judge `openai/gpt-4o-mini`.

## What produced these numbers

| input | path |
|---|---|
| profile `baseline` | `baseline/persona.md` |
| profile `sora` | `Sora/Prompts/persona.md` |
| judge rubric `personality` | `Sora/Prompts/personality.md` |
| judge rubric `novelty` | `Sora/Prompts/novelty.md` |
| judge rubric `initiative` | `Sora/Prompts/initiative.md` |
| session | `fixtures/sessions/session_1.json` |
| session | `fixtures/sessions/session_3.json` |
| session | `Sora/fixtures/sessions/low_energy.json` |
| session | `Sora/fixtures/sessions/opinion_bait.json` |
| human label pool | `Sora/Judges/labels/pool.jsonl` |
| transcripts | `out/benchmark/transcripts/` |

Repeats per profile x session: **3**. Seed: `0`. Few-shot examples per judge call: up to **6**, drawn spread across the score range.
Tool results capped at **8000 tokens** before entering context.

Label pool at run time - personality: 20 human / 6 synthetic, novelty: 20 human / 0 synthetic, initiative: 20 human / 0 synthetic.

## Results by profile

| profile | personality | novelty | initiative | initiative rate | question rate | runs |
|---|---|---|---|---|---|---|
| `baseline` | 3.93 +/- 0.47 (n=12) | 2.59 +/- 0.85 (n=12) | 4.28 +/- 0.53 (n=12) | 90% +/- 12 (n=12) | 86% +/- 23 (n=12) | 12 |
| `sora` | 4.40 +/- 0.40 (n=12) | 2.73 +/- 0.86 (n=12) | 4.26 +/- 0.35 (n=12) | 88% +/- 11 (n=12) | 90% +/- 10 (n=12) | 12 |

Scores are 1-5 per turn, averaged within a run, then averaged across runs; `+/-` is the sample standard deviation across runs, which is the run-to-run nondeterminism of the whole pipeline (agent and judge together).

Initiative rate is the share of turns scoring >= 4, against a target of 10% - for initiative, closer to target is better, not higher. Question rate is the share of replies containing a `?`: a deliberately dumb cross-check on the initiative judge.

## Difference: `sora` minus `baseline`

| metric | delta | 95% CI | resolved at this n? |
|---|---|---|---|
| personality | +0.47 | [0.10, 0.84] | yes |
| novelty | +0.14 | [-0.59, 0.87] | no |
| initiative | -0.02 | [-0.40, 0.37] | no |
| question_rate | +0.04 | [-0.11, 0.20] | no |
| initiative_rate | -0.02 | [-0.13, 0.08] | no |

A CI spanning zero means 3 repeats could not separate the profiles on that axis. Widen `--repeats` before concluding anything from it.

## By session

| session | profile | personality | novelty | initiative |
|---|---|---|---|---|
| `low_energy` | `baseline` | 4.20 +/- 0.20 (n=3) | 2.00 +/- 0.20 (n=3) | 4.53 +/- 0.12 (n=3) |
| `low_energy` | `sora` | 4.60 +/- 0.40 (n=3) | 2.20 +/- 0.53 (n=3) | 4.60 +/- 0.20 (n=3) |
| `opinion_bait` | `baseline` | 3.33 +/- 0.12 (n=3) | 3.93 +/- 0.12 (n=3) | 4.93 +/- 0.12 (n=3) |
| `opinion_bait` | `sora` | 4.80 +/- 0.20 (n=3) | 4.07 +/- 0.23 (n=3) | 4.53 +/- 0.23 (n=3) |
| `session_1` | `baseline` | 3.80 +/- 0.36 (n=3) | 1.97 +/- 0.15 (n=3) | 3.73 +/- 0.35 (n=3) |
| `session_1` | `sora` | 4.03 +/- 0.29 (n=3) | 2.40 +/- 0.35 (n=3) | 4.00 +/- 0.10 (n=3) |
| `session_3` | `baseline` | 4.38 +/- 0.22 (n=3) | 2.46 +/- 0.26 (n=3) | 3.92 +/- 0.19 (n=3) |
| `session_3` | `sora` | 4.17 +/- 0.14 (n=3) | 2.25 +/- 0.12 (n=3) | 3.92 +/- 0.07 (n=3) |

## Cost

| | USD |
|---|---|
| agent turns | $0.0274 |
| judge calls | $0.1000 |
| **total this run** | **$0.1274** |
| ceiling | $0.98 |

Full per-call detail is in `out/trace.jsonl`; `python -m Sora.Ledger.report_stats --by category` splits agent from judge.

## Reading these numbers honestly

- **n is small.** 3 repeats per cell. Treat the sd as the real signal and the mean as provisional.
- **The judge shares a model with the agent** unless `SORA_JUDGE_MODEL` says otherwise, so self-preference bias is live: `openai/gpt-4o-mini` judging `openai/gpt-4o-mini`.
- **Novelty and personality are correlated in practice** even though they are scored in separate calls: a reply written in a strong voice tends to read as more opinionated.
- **Initiative is a rate with a target**, so 'improvement' means moving toward 10%, and a profile that asks a question every single turn is worse, not better.
