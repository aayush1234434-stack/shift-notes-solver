# Design

The job is to get a weak model, Granite 4.2 8B, to turn noisy shift notes into a schedule, without fine-tuning and without a stronger model in the answering path. One-shot, with no harness, Granite scored 0% macro exact match on the 60 visible items. A frontier model, used only as a comparison, scored 67.5% on an earlier pair of runs. The gap is the architecture.

## What I tried first

I asked Granite to emit the constraint object: people, blocks, stations, and a list of typed rules with quotes from the notes. Z3 would solve whatever survived validation. That split sounds right, and it was the wrong interface for this model.

The replies were often not valid JSON. When they were, the types were wrong, "later than" came back as the opposite order, and the person field was sometimes a phrase like "whoever has intake" instead of a name from the header. Validation dropped those rules. Too few rules means more than four schedules, which this task does not allow, or an empty inconsistent answer. An empty conflict list scores zero, same as a blank assignment. Looking at completed 1× outputs from that setup, unique items were around one in ten and ambiguous and inconsistent items were not coming back right.

I then let a sentence parser replace the model's rules whenever the line matched a pattern. On the visible 60 that scored perfectly, including when the model reply was thrown away. That is not a legal system. The assignment checks that answers change when the model output changes. That overwrite is gone from the repo. The parser can propose readings. It does not accept them.

## What runs now

Constraint extraction is model-based. Python prints a menu of readings the named entities can support. Granite picks one letter, or `X`. The parser does not accept a reading by itself.

Python reads the header. Those lists are regular, and asking Granite to copy them is how fake names got in.

For each note line, Python writes a few readings the entities can support. A directional line gets both orders. A line about last month still gets a reading, so the model can reject it. A car-share, or any line with names but no order, gets no reading, only `X`. Granite answers with the line number and one letter:

```
2 A
3 X
```

A letter that is not on that line's menu is ignored. So is a missing line. An accepted letter is saved with the full original line as the citation, hedge included. Z3 enumerates. One solution is unique. Two to four are ambiguous, and all of them are returned. Zero is inconsistent: we search for a set of accepted lines that cannot all hold, such that dropping any one of them leaves a set that can. Header facts, such as one person per block, are background. They are not part of the citation.

At 3× there is a review call that can change a letter, and a follow-up if some lines with options were never answered. At 10× the spare calls go back over lines that have options, never past ten calls for that item. On the visible set the third 3× slot never fired: every option-line was answered the first time, so each item used two calls. At 10× almost every item used the full ten.

## Why each piece is there

The header parser exists so names and times stay exact. The menu exists because Granite is bad at inventing a schema and good enough, sometimes, at picking between two printed sentences. `X` exists so social lines, old rotas, and open questions are the model's decision. The menu is built from the names, times, and stations in the sentence, not from a catalog of note phrases. I removed that catalog. Phrases such as "opens up at" and "whoever drew the" were copied from the visible set, and the held-out notes will not use them.

Validation exists so a wrong or invented letter cannot smuggle in a person or a quote. The citation is the source line, not Granite's paraphrase. Z3 exists so "all of the schedules" and "a minimal conflict" are checked rather than guessed. The 3× and 10× calls exist because temperature is 1.0 and a single letter is noisy. They are capped because the budget is part of the grade, and 1× counts at least as much as 10×.

Five ablations are wired, one component off at a time: the worked examples in the prompt, source-line checks, the review call, the extra line batches, and the minimal-conflict search. `python -m src.evaluate` runs them, and the table is in the README. On the submitted prompt, two runs per cell, the conflict search is the piece whose removal is unambiguous: inconsistent items fall to zero and macro drops about 10 to 14 points. Removing the worked examples hurts, most clearly at 10× (28.3% against 41.7%). Source validation does nothing on this path, because every accepted letter already carries the full source line. The review call and the extra batches do not improve the score. I would not defend those two as load-bearing.

## The numbers

The submitted prompt is SHA-256 `e89a46577c8c4eac9d1c1266e4d5300d26a665993c43b38ef2eb390fa0e9624b`. The ablation table, two runs per cell on the 60 visible items, scores 40.8% at 1×, 40.8% at 3×, and 41.7% at 10×. Six further 1× runs of that same file average 45.0%, standard deviation 2.5. All eight 1× runs together average 44.0%, standard deviation 2.9. I am not averaging those into the 3× or 10× cells, because those budgets were only measured inside the table.

A previous draft, hash `6b74f5d9…`, averaged 45.3% over six 1× runs. It is not the file in the repo. The prompt before this rewrite, hash `a3663d77…`, was about 25% at 1×. An older 31.67% live run and a 100% fixed-letter replay are earlier still, and the replay never called Granite.

## Where it breaks

I logged the letter Granite picked on the six 1× runs of the submitted prompt and compared each line with the reading the line should have. Wrong direction was about 3% of real rules. Accepting a distractor that has a menu was about 10% of those lines. Dropping a real rule, answering X, was about 13%, and it clustered on hedged sentences and on "between".

The prompt before the rewrite had the opposite shape: it accepted about 69% of distractor lines that had a menu. That is what the rewrite was aimed at. It worked, and it also made Granite drop more real rules. Ambiguous items improved. Inconsistent items did not, because a citation that is one line short still scores zero.

I checked that trade on a small probe I wrote myself: 20 lines, names and wording that are not in the visible set, five repeats. It is not a sample from the benchmark. On the old prompt, false accepts were 20 of 45 distractor answers and missed rules were 5 of 50. On the submitted prompt, false accepts were 0 of 45 and missed rules were 13 of 50. False acceptance improved and false rejection of real rules got worse. That is why the visible-set score did not rise by as much as the distractor count suggested. Two of the misses were stable: "Reckon Priya is on the 13:00 block" and "If I remember right, Omar is not on the 09:00 block" were dropped on all five repeats.

A third break is the menu itself. Granite cannot select a constraint that was not printed. Lines that name a person and a block still get "works this block" and "does not." Lines with an ordinary order word ("before", "sooner", "ahead", "follows") get both directions even when the sentence is not one of the visible templates. A sentence that states an order in some other way, or that never uses the header's names, has nothing to pick. The held-out set is written from a different phrasing pool. I have not run it. I would expect the score to drop where the new sentences do not hit those entity patterns, and to hold up where they still name the same people, times, and stations and still use ordinary order or assignment language.

If a different person wrote the notes by hand, I would trust the header parse only while the header kept this shape. I would trust the menu when the sentence mentions the real names and a block, a station, or an order. I would not trust it for nicknames, for a constraint split across two lines, or for order language outside the cue list. In those cases the model is stuck with `X`, and a real rule disappears. That is the trade I accepted when I stopped letting the model write free JSON. Free JSON could say anything, and for this model it usually said something illegal. A menu can only be wrong in the ways it lists.

I also tried offering order options for any line that names two people and nothing else, so "back to back" would have a letter to pick. On the previous prompt draft, six 1× runs with that menu averaged 40.8% and six without it averaged 45.3%, so I took the menu change out.

With more time I would spend the extra calls on the letters that made a rota unsatisfiable, not on rereading every line. The flat curve says the current extra calls are not doing that. I would also stop the conflict search from returning a set that is one statement too short. I would not go back to asking Granite for the constraint JSON.
