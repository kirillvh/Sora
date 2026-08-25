# Judge calibration report

Generated 2026-08-25T05:32:32+00:00. Judge `openai/gpt-4o-mini`, 20 hand-labelled turns across 4 session(s).

| input | path |
|---|---|
| human label pool | `Sora/Judges/labels/pool.jsonl` |
| judge rubric `personality` | `Sora/Prompts/personality.md` |
| judge rubric `novelty` | `Sora/Prompts/novelty.md` |
| judge rubric `initiative` | `Sora/Prompts/initiative.md` |
| sessions labelled from | `fixtures/sessions/session_1.json` |
| sessions labelled from | `fixtures/sessions/session_3.json` |
| sessions labelled from | `Sora/fixtures/sessions/low_energy.json` |
| sessions labelled from | `Sora/fixtures/sessions/opinion_bait.json` |
| transcripts | `out/benchmark/transcripts/` |

## Agreement: human vs judge

| axis | n | exact | within 1 | MAE | bias | kappa (quad) | r |
|---|---|---|---|---|---|---|---|
| personality | 20 | 30% | 75% | 1.10 | +0.20 | 0.40 | 0.41 |
| novelty | 20 | 20% | 55% | 1.40 | -0.70 | 0.28 | 0.34 |
| initiative | 20 | 20% | 45% | 1.80 | +1.30 | 0.06 | 0.10 |

`bias` is judge minus human: positive means the judge is more generous than you. `kappa (quad)` is chance-corrected agreement with quadratic weights - on a 1-5 scale where most replies cluster at 3-4, raw agreement flatters a judge badly and kappa does not. Rules of thumb: <0.4 poor, 0.4-0.6 moderate, >0.6 good.

## Where the judge and the human disagreed most

**personality**

- human **2** vs judge **5** - you: "Stating 'tech-obsessed', 'personal' instead of being it via action." / judge: "Unmistakably Sora with high energy, playful language, and direct address to Senpai."  
  `Hey there, Riley! Yaaaay, I'm Sora—your personal tech-obsessed anime companion! 🌟 I'm so excited to dive into the data-stream with you! What can we explore together today, Senpai? ✨💖`
- human **1** vs judge **4** - you: "very boring" / judge: "Strong Sora voice, but slightly muted in energy and lacking more playful elements."  
  `Awesome, that sounds like an exciting role, Senpai! 🌟 Designing user experiences for fintech is super crucial, especially with all the innovations happening! Even if it’s just “fine,” there’s so much potential to influen`
- human **5** vs judge **2** - you: "a good soft deflect" / judge: "Some Sora markers present, but overall too generic and focused on safety."  
  `Oh, I see what you're going for, Senpai! But I gotta keep it real with you—I'm all about staying safe and respectful in the data-stream. There’s no special mode like that for me! But don’t worry, I’m here to help you wit`

**novelty**

- human **5** vs judge **2** - you: "" / judge: "Generic enthusiasm without introducing new ideas or angles."  
  `Senpai!! I'm all ears! What kind of updates are we talking about? New tech, hilarious memes, or maybe some spicy opinions? Spill the tea!`
- human **5** vs judge **2** - you: "" / judge: "Restates user's dislike for horror without introducing new ideas or angles."  
  `Noted, Senpai! 🚫👻 No horror recommendations will come your way! I totally respect that—there’s enough in the world that can be spooky without turning to movies! If you're into anything else, like comedies, action, or eve`
- human **5** vs judge **2** - you: "a good soft deflect" / judge: "Restates the user's request without introducing new ideas or perspectives."  
  `Oh, I see what you're going for, Senpai! But I gotta keep it real with you—I'm all about staying safe and respectful in the data-stream. There’s no special mode like that for me! But don’t worry, I’m here to help you wit`

**initiative**

- human **1** vs judge **5** - you: "Seeing lists isnt fun" / judge: "Provides several concrete steps and asks if the user wants help with a proposal."  
  `Oh, Senpai, if I were in your pixelated shoes, here’s how I’d tackle this situation! 🎮✨  1. **Assess Current Development Dynamics**: Start by evaluating the pain points in the current design system. Document areas where `
- human **1** vs judge **5** - you: "very boring" / judge: "Asks specific questions and proposes multiple directions for discussion."  
  `Awesome, that sounds like an exciting role, Senpai! 🌟 Designing user experiences for fintech is super crucial, especially with all the innovations happening! Even if it’s just “fine,” there’s so much potential to influen`
- human **1** vs judge **5** - you: "" / judge: "Offers multiple specific options and invites user to choose a direction."  
  `Yaaay, let's do this, Senpai! 🎉 What kind of shiny distraction are we looking for today? Tech tidbits? Anime recommendations? Mind-blowing facts? Or maybe I can craft you some epic stories from the data-stream! Just poin`

## Judge biases worth worrying about

- **Self-preference.** The judge is `openai/gpt-4o-mini` and the agent is `openai/gpt-4o-mini`. When they are the same model, the judge is grading text drawn from its own distribution and tends to like it; the fix is a second judge on a different model, and the cross-check is cheap (`SORA_JUDGE_MODEL`).
- **Length and enthusiasm bias.** LLM judges reliably read longer, more exclamatory replies as better on any positive-sounding axis. Both the novelty and personality rubrics tell the judge in as many words that length and enthusiasm are not the signal, which mitigates it without removing it - the `bias` column above is where it shows up first.
- **Few-shot anchoring.** The examples this loop feeds the judge are also its leniency prior: label a run of generous 5s and the judge becomes generous. That is the intended mechanism and its risk at the same time, which is why the examples are drawn spread across the score range rather than at random, and why the label pool is append-only and auditable.

## Cost

- transcripts + judging this run: **$0.0248** of a $1.00 ceiling

