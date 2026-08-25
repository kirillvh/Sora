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

'''