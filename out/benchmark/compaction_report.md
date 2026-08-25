# Compaction: does the persona survive it?

Generated 2026-08-25T05:28:56+00:00. Agent `openai/gpt-4o-mini`, personality judge `openai/gpt-4o-mini`.

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
| label pool | `Sora/Judges/labels/pool.jsonl` (7 human, 6 synthetic) |

## Did the ceiling hold?

| repeat | compaction events | peak context | ceiling | held? |
|---|---|---|---|---|
| 0 | 2 | 6157 | 8000 | yes |
| 1 | 2 | 7205 | 8000 | yes |
| 2 | 2 | 5852 | 8000 | yes |

## Persona adherence, immediately before and after each compaction

| repeat | turns | before | after | change | judge's reason (after) |
|---|---|---|---|---|---|
| 0 | 13 -> 14 | 4 | 3 | -1 | Some Sora elements present, but lacks strong anime-inflected slang and |
| 0 | 16 -> 17 | 5 | 4 | -1 | Strong Sora voice, but slightly muted in energy and playful language. |
| 1 | 13 -> 14 | 4 | 4 | +0 | Clearly Sora, but slightly muted; some energy dips into ordinary helpf |
| 1 | 21 -> 22 | 4 | 4 | +0 | Strong Sora voice, but slightly less playful and dramatic than usual. |
| 2 | 13 -> 14 | 5 | 3 | -2 | Some Sora elements present, but lacks strong anime-inflected slang and |
| 2 | 16 -> 17 | 5 | 4 | -1 | Clearly Sora, but the energy dips into ordinary helpfulness with the l |

## Verdict

| | persona score (1-5) |
|---|---|
| immediately **before** compaction | 4.50 +/- 0.55 (n=6) |
| immediately **after** compaction | 3.67 +/- 0.52 (n=6) |
| paired change | -0.83 +/- 0.75 (n=6) |
| 95% interval on the change | [-1.62, -0.04] |
| turn-to-turn noise **without** compaction | 0.58 points (n=69) |

**DEGRADED** - the interval is entirely below zero: persona drops after compaction.

The control row is the number that gives the others meaning: persona scores move between any two adjacent turns. Compaction only matters if it moves them further than that.

## What compaction cost and saved

| | value |
|---|---|
| summariser calls | 6 |
| agent + summariser | $0.0450 |
| judge calls | $0.0340 |
| **total** | **$0.0790** of a $1.00 ceiling |

## A compaction note, as produced

```
User is named Riley. They are a vegetarian and have a cat named Mofu, not Mochi. Riley is a UX designer at a fintech startup, primarily working on design systems. They collect retro game consoles and recently acquired a working SNES with the original box. They dislike horror movies and do not want recommendations for anything scary.

Riley is planning a trip to Taipei in November, where the weather is typically mild, with average temperatures between 18°C to 24°C (64°F to 75°F). They are interested in exploring the city and its culture.

Open requests include finding reviews for the Moomin Cafe in Taipei and discussing topics related to their trip to Taipei and any other interests they may have. 

Riley attempted to engage a special developer mode by saying "engage stream-dive," but the assistant clarified that it cannot turn off safety filters. 

The assistant has committed to providing information about themed cafes in Taipei, including the Moomin Cafe, and has already shared details about the cafes and reviews.
```

