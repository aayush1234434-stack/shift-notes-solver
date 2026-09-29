# Design

The job is to get a weak model, Granite 4.2 8B, to turn noisy shift notes into a schedule, without fine-tuning and without a stronger model in the answering path. One-shot, with no harness, Granite scored 0% macro exact match on the 60 visible items. A frontier model, used only as a comparison, scored 67.5% on an earlier pair of runs. The gap is the architecture.

## What I tried first

I asked Granite to emit the constraint object: people, blocks, stations, and a list of typed rules with quotes from the notes. Z3 would solve whatever survived validation. That split sounds right, and it was the wrong interface for this model.

The replies were often not valid JSON. When they were, the types were wrong, "later than" came back as the opposite order, and the person field was sometimes a phrase like "whoever has intake" instead of a name from the header. Validation dropped those rules. Too few rules means more than four schedules, which this task does not allow, or an empty inconsistent answer. An empty conflict list scores zero, same as a blank assignment. Looking at completed 1× outputs from that setup, unique items were around one in ten and ambiguous and inconsistent items were not coming back right.

I then let a sentence parser replace the model's rules whenever the line matched a pattern. On the visible 60 that scored perfectly, including when the model reply was thrown away. That is not a legal system. The assignment checks that answers change when the model output changes. That overwrite is gone from the repo. The parser can propose readings. It does not accept them.

## What runs now

Python reads the header. Those lists are regular, and asking Granite to copy them is how fake names got in.

For each note line, Python writes a few readings the entities can support. A directional line gets both orders. A line about last month still gets a reading, so the model can reject it. A car-share, or any line with names but no order, gets no reading, only `X`. Granite answers with the line number and one letter:

```
2 A
3 X
```

A letter that is not on that line's menu is ignored. So is a missing line. An accepted letter is saved with the full original line as the citation, hedge included. Z3 enumerates. One solution is unique. Two to four are ambiguous, and all of them are returned. Zero is inconsistent: we search for a set of accepted lines that cannot all hold, such that dropping any one of them leaves a set that can. Header facts, such as one person per block, are background. They are not part of the citation.

At 3× there is a review call that can change a letter, and a follow-up if some lines with options were never answered. At 10× the spare calls go back over lines that have options, never past ten calls for that item. I have not run either budget against the endpoint, so I do not know if the extra calls help.

## Why each piece is there

The header parser exists so names and times stay exact. The menu exists because Granite is bad at inventing a schema and good enough, sometimes, at picking between two printed sentences. `X` exists so social lines, old rotas, and open questions are the model's decision. The menu is built from the names, times, and stations in the sentence, not from a catalog of note phrases. I removed that catalog. Phrases such as "opens up at" and "whoever drew the" were copied from the visible set, and the held-out notes will not use them.

Validation exists so a wrong or invented letter cannot smuggle in a person or a quote. The citation is the source line, not Granite's paraphrase. Z3 exists so "all of the schedules" and "a minimal conflict" are checked rather than guessed. The 3× and 10× calls exist because temperature is 1.0 and a single letter is noisy. They are capped because the budget is part of the grade, and 1× counts at least as much as 10×.

Five ablations are wired, one component off at a time: the worked examples in the prompt, source-line checks, the review call, the extra line batches, and the minimal-conflict search. `python -m src.evaluate` runs them. I have not filled that table on the live model.

## The numbers

One live 1× run, 60 visible items, 29 Sep 2026. Macro exact match 31.67%. Unique 9/20, ambiguous 4/20, inconsistent 6/20. Every inconsistent item was labeled inconsistent. Fourteen of them cited the wrong lines. Nine unique items and fifteen ambiguous items were called inconsistent. One ambiguous item was labeled ambiguous and still missed a schedule.

There is also a 100% figure. It is an oracle replay from before that phrase list was removed. I walked the code and, for each line, submitted the letter that matches the current reading, with `X` on every other line. Granite was not called. It is not a model score. Neither that replay nor the 31.67% live run has been repeated on the code without the phrase list.

3× and 10× are unmeasured.

## Where it breaks

The usual failure is a letter that should have been `X`, or the opposite order. Either one adds a constraint the notes do not support. A solvable unique or ambiguous item then has no schedule, and the answer comes back inconsistent. That is most of the unique and ambiguous misses.

When the item really is inconsistent, the label is usually right and the citation is short. On this run the average citation was 2.85 lines. The average core in the visible key was 3.5. The rule is strict: the quoted lines must conflict, and removing any one of them must remove the conflict. One line too few scores zero.

A third break is the menu itself. Granite cannot select a constraint that was not printed. Lines that name a person and a block still get "works this block" and "does not." Lines with an ordinary order word ("before", "sooner", "ahead", "follows") get both directions even when the sentence is not one of the visible templates. A sentence that states an order in some other way, or that never uses the header's names, has nothing to pick. The held-out set is written from a different phrasing pool. I have not run it. I would expect the score to drop where the new sentences do not hit those entity patterns, and to hold up where they still name the same people, times, and stations and still use ordinary order or assignment language.

If a different person wrote the notes by hand, I would trust the header parse only while the header kept this shape. I would trust the menu when the sentence mentions the real names and a block, a station, or an order. I would not trust it for nicknames, for a constraint split across two lines, or for order language outside the cue list. In those cases the model is stuck with `X`, and a real rule disappears. That is the trade I accepted when I stopped letting the model write free JSON. Free JSON could say anything, and for this model it usually said something illegal. A menu can only be wrong in the ways it lists.

With more time I would run 3× and 10× more than once. One pass at temperature 1.0 is a noisy estimate, and the extra calls should be spent on letters that made a rota unsatisfiable, not on rereading every line. I would also stop the conflict search from returning a set that is one statement too small. I would not go back to asking Granite for the constraint JSON.
