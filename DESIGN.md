# Design

The job is to get Granite 4.2 8B to turn noisy shift notes into a schedule, without fine-tuning and without a stronger model in the answering path. One-shot, with no harness, Granite scored 0% macro exact match on the 60 visible items. A frontier model, used only as a comparison, scored 67.5% on an earlier pair of runs. The gap is the architecture.

## What I tried first

I asked Granite to emit the constraint object: people, blocks, stations, and typed rules with quotes from the notes. Z3 would solve whatever survived validation. The replies were often not valid JSON. When they were, the types were wrong, "later than" came back as the opposite order, and the person field was sometimes a phrase like "whoever has intake". Validation dropped those rules. Too few rules means more than four schedules, which this task does not allow, or an empty inconsistent answer, which scores zero. Unique items from that setup were around one in ten.

I then let a sentence parser replace the model's rules whenever a line matched a pattern. On the visible 60 that scored perfectly, including when the model reply was thrown away. The assignment checks that answers change when the model output changes, so that overwrite is gone. The parser can propose readings. It does not accept them.

## What runs now

Constraint extraction is model-based. Python prints a menu of readings the named entities can support. Granite picks one letter, or `X`. The parser does not accept a reading by itself.

Python reads the header, because asking Granite to copy the staff list is how fake names got in. For each note line it writes a few readings the entities can support. A directional line gets both orders. A line about last month still gets a reading, so the model can reject it. A car-share, or any line with names but no order word, gets no reading, only `X`. Granite replies with the line number and one letter. A letter that is not on that line's menu, and a missing line, add no rule. An accepted letter is saved with the full original line as the citation, hedge included.

Z3 enumerates. One solution is unique. Two to four are ambiguous, and all of them are returned. Zero is inconsistent: we search for a set of accepted lines that cannot all hold, such that dropping any one of them leaves a set that can. Header facts, such as one person per block, are background and are not part of the citation.

At 3× a review call can change a letter, with a follow-up for option-lines never answered. At 10× spare calls reread lines that have options, never past ten. On the submitted prompt those extra calls do not raise the score.

## Why each piece is there

The header parser keeps names and times exact. The menu exists because Granite is bad at inventing a schema and better, sometimes, at picking between printed sentences. `X` exists so social lines, old rotas, and open questions are the model's decision. The menu is built from the names, times, and stations in the sentence, not from a catalog of note phrases. I removed that catalog. Phrases such as "opens up at" and "whoever drew the" were copied from the visible set, and the held-out notes will not use them.

Validation stops a wrong letter from smuggling in a person or a quote, and the citation is the source line. Z3 checks the schedules and the minimal conflict instead of guessing them. The extra calls exist because temperature is 1.0, and they are capped because 1× counts at least as much as 10×.

Five ablations turn one component off at a time: the worked examples, source-line checks, the review call, the extra line batches, and the minimal-conflict search. `python -m src.evaluate` runs them. The table is in the README. On the submitted prompt, two runs per cell, removing the conflict search drops inconsistent items to zero and macro by about 10 to 14 points. Removing the worked examples hurts, most clearly at 10× (28.3% against 41.7%). Source validation does nothing here, because every accepted letter already carries the full source line. The review call and the extra batches do not improve the score. I would not defend those two as load-bearing.

## The numbers

The submitted prompt is SHA-256 `e89a46577c8c4eac9d1c1266e4d5300d26a665993c43b38ef2eb390fa0e9624b`. The ablation table, two runs per cell on the 60 visible items, scores 40.8% at 1×, 40.8% at 3×, and 41.7% at 10×. Six further 1× runs of that same file average 45.0%, standard deviation 2.5. All eight 1× runs together average 44.0%, standard deviation 2.9. The six extra runs are not folded into the 3× or 10× cells. Those budgets were only measured inside the table.

A previous draft, hash `6b74f5d9…`, averaged 45.3% over six 1× runs and is not the file in the repo. The prompt before this rewrite, hash `a3663d77…`, was about 25% at 1×. An older 31.67% live run and a 100% fixed-letter replay are earlier still. The replay never called Granite.

## Where it breaks

On the six 1× runs of the submitted prompt I compared each letter with the reading the line should have. Wrong direction was about 3% of real rules. Accepting a distractor that has a menu was about 10% of those lines. Dropping a real rule was about 13%, clustered on hedged sentences and on "between". The prompt before the rewrite accepted about 69% of distractor lines that had a menu. The rewrite cut that, and it also made Granite drop more real rules. Ambiguous items improved. Inconsistent items did not, because a citation one line short scores zero.

I checked the trade on a probe I wrote: 20 lines, names and wording not in the visible set, five repeats. It is not a sample from the benchmark. On the old prompt, false accepts were 20 of 45 and missed rules were 5 of 50. On the submitted prompt, false accepts were 0 of 45 and missed rules were 13 of 50. False acceptance improved and false rejection got worse, which is why the visible score rose by less than the distractor count. "Reckon Priya is on the 13:00 block" and "If I remember right, Omar is not on the 09:00 block" were dropped every time.

Granite cannot select a constraint that was not printed. A person plus a block gets "works" and "does not." An ordinary order word ("before", "sooner", "ahead", "follows") gets both directions even when the sentence is not a visible template. Other order language, or a sentence that never uses the header's names, has nothing to pick. I also tried order options for any line that names two people and nothing else, so "back to back" would have a letter. On the previous prompt draft, six 1× runs with that menu averaged 40.8% and six without it averaged 45.3%, so I took the change out.

If someone else wrote the notes, I would trust the header only while it kept this shape, and the menu only when the sentence names the real people and a block, a station, or an order word. Nicknames, a constraint split across two lines, and other order language leave the model with `X`, so a real rule disappears. That is the cost of refusing free JSON, which for this model usually said something illegal.

The held-out set uses a different phrasing pool and has not been run. With more time I would spend spare calls on the letters that made a rota unsatisfiable, since the flat curve says the current extra calls do not, and I would stop the conflict search from returning a set one statement too short.
