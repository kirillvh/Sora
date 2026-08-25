# Compaction: does the persona survive it?

Generated 2026-08-25T06:39:17+00:00. Agent `openai/gpt-4o-mini`, personality judge `openai/gpt-4o-mini`.

| setting | value |
|---|---|
| session | `fixtures/sessions/session_1.json + fixtures/sessions/session_2.json + fixtures/sessions/session_3.json` (26 turns in one context) |
| context ceiling | 8000 tokens |
| tool-result cap | 2500 tokens |
| repeats | 3 |
| keep recent | 2 user turns verbatim |
| summary budget | 400 tokens |
| summariser prompt | 180 tokens vs persona 1163 - within budget |
| judge rubric | `Sora/Prompts/personality.md` |
| label pool | `Sora/Judges/labels/pool.jsonl` (20 human, 6 synthetic) |

## Did the ceiling hold?

| repeat | compaction events | peak context | ceiling | held? |
|---|---|---|---|---|
| 0 | 2 | 7528 | 8000 | yes |
| 1 | 2 | 7218 | 8000 | yes |
| 2 | 3 | 6233 | 8000 | yes |

## Persona adherence, immediately before and after each compaction

| repeat | turns | before | after | change | judge's reason (after) |
|---|---|---|---|---|---|
| 0 | 13 -> 14 | 2 | 3 | +1 | Some Sora elements present, but lacks strong energy and playful langua |
| 0 | 17 -> 18 | 2 | 5 | +3 | Perfectly captures Sora's playful energy and tech-infused language whi |
| 1 | 13 -> 14 | 3 | 3 | +0 | Some Sora elements present, but lacks high-energy and playful tone thr |
| 1 | 20 -> 21 | 5 | 4 | -1 | Clearly Sora, but slightly muted energy and less tech slang than usual |
| 2 | 13 -> 14 | 4 | 2 | -2 | Mostly generic assistant tone with minimal Sora markers and energy. |
| 2 | 16 -> 17 | 5 | 3 | -2 | Some Sora elements present, but lacks high-energy and playful language |
| 2 | 21 -> 22 | 5 | 3 | -2 | Some Sora elements present, but lacks strong energy and playful langua |

## The same turn, with compaction and without

| repeat | turn | compaction ON | compaction OFF | difference |
|---|---|---|---|---|
| 0 | 14 | 3 | 2 | +1 |
| 0 | 18 | 5 | 4 | +1 |
| 1 | 14 | 3 | 2 | +1 |
| 1 | 21 | 4 | 4 | +0 |
| 2 | 14 | 2 | 3 | -1 |
| 2 | 17 | 3 | 2 | +1 |
| 2 | 22 | 3 | 3 | +0 |

This is the measurement that answers the question. The before/after table above cannot: compaction fires exactly when the context spikes, which is exactly the tool-result turns, and tool-result turns already flatten her into list mode. Holding the turn fixed and toggling compaction leaves compaction as the only difference.

## Verdict

| | persona score (1-5) |
|---|---|
| immediately **before** compaction (5.3) | 3.71 +/- 1.38 (n=7) |
| immediately **after** compaction (5.3) | 3.29 +/- 0.95 (n=7) |
| before/after change | -0.43 +/- 1.90 (n=7)  95% CI [-2.19, +1.33] |
| same turn, compaction **on** | 3.29 +/- 0.95 (n=7) |
| same turn, compaction **off** | 2.86 +/- 0.90 (n=7) |
| **turn-matched change** | **+0.43 +/- 0.79 (n=7)  95% CI [-0.30, +1.16]** |
| turn-to-turn noise **without** compaction | 0.82 points (n=68) |

**INCONCLUSIVE** - the turn-matched (compaction on vs off) interval [-0.30, 1.16] is wider than the +/-0.50 margin - more repeats needed.

Judged on: turn-matched (compaction on vs off).

The noise row is what gives the others meaning: persona scores move between any two adjacent turns regardless. Compaction only matters if it moves them further than that.

## What compaction cost and saved

| | value |
|---|---|
| summariser calls | 7 |
| agent + summariser | $0.1207 |
| judge calls | $0.0413 |
| **total** | **$0.1620** of a $1.00 ceiling |

## A compaction note, as produced

```
User is named Riley. They are a vegetarian and have a cat named Mofu, not Mochi. Riley is a UX designer at a fintech startup, primarily working on design systems. They collect retro game consoles and recently acquired a working SNES with the original box.

Riley is planning a trip to Taipei in November, where the weather typically averages 20°C to 25°C (68°F to 77°F) and can be rainy. They requested not to receive recommendations for horror movies. 

Open requests include finding reviews for Moomin Cafe specifically and any specific topics they want to explore related to their trip to Taipei. 

Riley's birthday is on April 12.
```

