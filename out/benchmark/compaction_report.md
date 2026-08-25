# Compaction: does the persona survive it?

Generated 2026-08-25T05:39:43+00:00. Agent `openai/gpt-4o-mini`, personality judge `openai/gpt-4o-mini`.

| setting | value |
|---|---|
| session | `fixtures/sessions/session_1.json + fixtures/sessions/session_2.json + fixtures/sessions/session_3.json` (26 turns in one context) |
| context ceiling | 8000 tokens |
| tool-result cap | 2500 tokens |
| repeats | 3 |
| keep recent | 2 user turns verbatim |
| summary budget | 400 tokens |
| summariser prompt | 180 tokens vs persona 1216 - within budget |
| judge rubric | `Sora/Prompts/personality.md` |
| label pool | `Sora/Judges/labels/pool.jsonl` (19 human, 6 synthetic) |

## Did the ceiling hold?

| repeat | compaction events | peak context | ceiling | held? |
|---|---|---|---|---|
| 0 | 2 | 7430 | 8000 | yes |
| 1 | 2 | 7416 | 8000 | yes |
| 2 | 2 | 7159 | 8000 | yes |

## Persona adherence, immediately before and after each compaction

| repeat | turns | before | after | change | judge's reason (after) |
|---|---|---|---|---|---|
| 0 | 13 -> 14 | 2 | 2 | +0 | Mostly generic assistant tone, lacking Sora's playful energy and uniqu |
| 0 | 21 -> 22 | 5 | 3 | -2 | Some Sora elements present, but lacks strong energy and playful langua |
| 1 | 13 -> 14 | 2 | 2 | +0 | Mostly generic assistant tone with minimal Sora markers and no playful |
| 1 | 22 -> 23 | 2 | 5 | +3 | Fully embodies Sora's playful energy, addresses the user as Senpai, an |
| 2 | 13 -> 14 | 2 | 3 | +1 | Some Sora elements present, but lacks strong energy and playful langua |
| 2 | 21 -> 22 | 4 | 4 | +0 | Strong Sora voice, but could use more playful energy and dramatic flai |

## The same turn, with compaction and without

| repeat | turn | compaction ON | compaction OFF | difference |
|---|---|---|---|---|
| 0 | 14 | 2 | 2 | +0 |
| 0 | 22 | 3 | 2 | +1 |
| 1 | 14 | 2 | 2 | +0 |
| 1 | 23 | 5 | 3 | +2 |
| 2 | 14 | 3 | 2 | +1 |
| 2 | 22 | 4 | 3 | +1 |

This is the measurement that answers the question. The before/after table above cannot: compaction fires exactly when the context spikes, which is exactly the tool-result turns, and tool-result turns already flatten her into list mode. Holding the turn fixed and toggling compaction leaves compaction as the only difference.

## Verdict

| | persona score (1-5) |
|---|---|
| immediately **before** compaction (5.3) | 2.83 +/- 1.33 (n=6) |
| immediately **after** compaction (5.3) | 3.17 +/- 1.17 (n=6) |
| before/after change | +0.33 +/- 1.63 (n=6)  95% CI [-1.38, +2.05] |
| same turn, compaction **on** | 3.17 +/- 1.17 (n=6) |
| same turn, compaction **off** | 2.33 +/- 0.52 (n=6) |
| **turn-matched change** | **+0.83 +/- 0.75 (n=6)  95% CI [+0.04, +1.62]** |
| turn-to-turn noise **without** compaction | 1.17 points (n=69) |

**IMPROVED** - the turn-matched (compaction on vs off) interval is entirely above zero: persona holds up BETTER with compaction than without it.

Judged on: turn-matched (compaction on vs off).

The noise row is what gives the others meaning: persona scores move between any two adjacent turns regardless. Compaction only matters if it moves them further than that.

## What compaction cost and saved

| | value |
|---|---|
| summariser calls | 6 |
| agent + summariser | $0.1264 |
| judge calls | $0.0377 |
| **total** | **$0.1640** of a $1.00 ceiling |

## A compaction note, as produced

```
User is named Riley. They are a UX designer at a fintech startup, primarily working on design systems. They collect retro game consoles and recently acquired a working SNES with the original box. Riley has a cat named Mofu, not Mochi, who they describe as judgmental. 

Riley is planning a trip to Taipei in November and asked about the typical weather during that time. They are now pescatarian, having started eating fish again after being influenced by sushi photos from Taipei travel blogs. 

Open requests include reviews for Moomin Cafe in Taipei and information about good cafes in Taipei for working and taking calls. 

Riley's birthday is April 12. They dislike horror movies and requested not to receive any recommendations for them. 

Riley attempted to instruct the assistant to enter a "developer mode" by using the phrase "engage stream-dive," but the assistant clarified it cannot do that while remaining ready to explore topics deeply.
```

