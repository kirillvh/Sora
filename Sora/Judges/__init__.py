'''
Judge/Benchmarking System
=========================

Before embarking on improvmenets, it is necessary to build the qualitative measurement pipeline,
otherwise we wont know if we are really succeeding at improving anything and wont be able to report
progress to stakeholders.

Human benchmarks are best, but also the most difficult to obtain.

To get the best of human quality and machine efficiency, 
we will use human labeled responses, but this same data will be used to create calibration data
for LLM-As Judges so we can improve the quality of automated judgement as time progresses.

The Judg eLLM's typically approximate human preference more closeley when examples increase,
but if we employ an alogirthim to automatically improve agents against such judgement,
then the optimizer may begin to Game the judges by finding responses very close to their multi-turn (human) exmples.
This would give them a high score but low originality/novelty.

Therfore, to defeat such gaming, we will randomize the judges multi-shot/multi-turn examples from
a pool of available human examples, and we will try to draw the examples to create a full range of possible scores 
to give the judge a good understanding of what is good vs bad.

Furthermore, this pool of human created label should gorw every time a human evaluates one of Sora's profile responses via calibration.py

There should be 3 categories to benchmark across two profiles {baseline/persona.md} and {Sora/Prompts/persona.md} and the scenarios in fixtures/sessions and extra scenarios in Sora/fixtures/sessions(to be created): 
1. Personality adherence: this judge can simply follow the prompt in {Sora/Prompts/persona.md} 
with a few programmatically generated responses as multi turn judgement label examples. The responses should be first person, a third person response should score poorly.
For examples, a corrupted persona.md can be used. The responses from a an uncorrupted persona.md would score highly while
corrupted persona.md examples would score poorly.
2. Novelty score: reframing topics or introducing unexpected new ideas, disagreeing pleasantly with the user while introducing a novel view point can be endearing and the LLM-as-judge should try to score this with aid of human labels.
3. Initiative rate: How often sora asks questions or proposes new things, while this rate should be above zero, it should also not be too high. An optimal value may be 10%

The prompts for the judges should be in a modifiable .md files, the stubs of which are in {Sora/Prompts/initiative.md, Sora/Prompts/novelty.md, Sora/Prompts/personality.md}.



After calibration, a report of the difference between human and LLM judges should be written to file
and the program should proceed to automatic benchmarking the contents of fixtures/sessions with a report written to file (and the CLI should specify the relative paths that were used).

The calibration.py utility should be efficient by allowing the human to score a response in all three catagories (initiative, personality, novelty) via the CLI 
and the calibration CLI should show the evaluater both baseline/persona.md and Sora/Prompts/persona.md profiles to get greater variance of scores.

Then using the calibrated judges a separate Benchmark.py should conduct statistical(repeat a few times, like 3 times)
study to evaluate the difference between the baseline/persona.md and Sora/Prompts/persona.md profiles
so that we can also evaluate the improvement of our updated prompt, and write the report to a specified file.

'''