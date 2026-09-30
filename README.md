# Shift notes solver

This program reads a facility rota and the notes that go with it, and it says which schedule those notes allow.

Each item is one of three kinds, and the file does not say which:

- **unique** — one assignment fits
- **ambiguous** — two, three, or four assignments fit, and the answer must list all of them
- **inconsistent** — no assignment fits, and the answer must quote a minimal set of note lines that contradict each other

People, blocks, stations, and station holders come from the header. The notes are hedged, repetitive, and full of lines that are not rules. A hedge such as "I'm fairly sure" is still a rule. An old arrangement, a wish, a failed request, small talk, or an unanswered question is not.

Granite does not return constraint JSON. For each note line, Python prints a short menu of legal readings. Granite picks one letter, or `X` if the line is not a current rule. Only that letter becomes a constraint. Z3 solves the constraints Granite accepted.

## Install

Python 3.12. From the repo root:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Put the endpoint key in `.env`. The file is gitignored. Do not commit it.

```bash
OPENROUTER_API_KEY=...
OPENROUTER_BASE_URL=...
MODEL=ibm-granite/granite-4.2-8b
```

`requirements.txt` pins `python-dotenv==1.1.1` and `z3-solver==4.15.3.0`.

## Run

```bash
./run items.json --budget 1x --out answers.json
```

`1x` is one model call per item. `3x` allows up to three, `10x` up to ten. Every item gets at least one call. A call that fails still uses its slot, and the client does not retry it. If the first call for an item fails, that item is written as an empty inconsistent answer, which scores zero, and the run continues.

```bash
./run items.json --budget 3x --out answers.json
./run items.json --budget 10x --out answers.json
```

`answers.json` is a map from item id to one answer object. `./run` does not read `visible_key.json`. Score a finished file with the scorer from the candidate package:

```bash
python3 score.py visible_key.json answers.json items.json
```

## How a run works

```
notes
  → header parse and a candidate menu per note line
  → Granite picks one letter per line
  → validation keeps on-menu letters and stores the full original line
  → Z3
  → answers.json
```

Python reads the header. It does not ask the model to copy the staff list. For each note line it lists up to four readings the words can bear, plus `X`. A directional sentence gets both orders. Granite replies like this:

```
2 A
3 X
```

`X`, a missing line, and a letter that was not printed for that line add no rule. An accepted letter is stored with the original note line as its citation. Z3 then enumerates schedules.

At `3x`, a second call can change a letter. If some option-lines were never answered, a third call asks only for those. At `10x`, the remaining calls go back over lines that have options, still inside the cap of ten.

The only prompt `./run` sends is `prompts/extract.txt`.

## Model

Every call uses `ibm-granite/granite-4.2-8b`, temperature `1.0`, `top_p` `0.95`, and reasoning disabled. The client sends `X-Item-Id` set to the item id. The client rejects any other model name.

## Answers

Unique, one schedule. Station holders include `station`. Everyone else has a block only.

```json
{"case": "unique", "assignment": {"Alice": {"block": "07:00", "station": "intake"}, "Bob": {"block": "09:00"}}}
```

Ambiguous, every fitting schedule. Order does not matter.

```json
{"case": "ambiguous", "assignments": [{"Alice": {"block": "07:00"}}, {"Alice": {"block": "09:00"}}]}
```

Inconsistent. Each string is one full note line. The set must be unsatisfiable, and dropping any one line must leave a satisfiable set.

```json
{"case": "inconsistent", "conflicts": ["Alice works at 07:00.", "Alice works at 09:00."]}
```

## What has been measured

Every number below is macro exact match on the 60 visible items. Temperature is 1.0. A prompt is identified by the SHA-256 of `prompts/extract.txt`, which is what `python -m src.evaluate` records.

The submitted prompt is `e89a46577c8c4eac9d1c1266e4d5300d26a665993c43b38ef2eb390fa0e9624b`.

The ablation table is two runs per cell of that prompt, 29 Sep 2026. Full system: **40.8% at 1×, 40.8% at 3×, 41.7% at 10×**. Per kind, exact: unique 37.5% / 32.5% / 35.0%, ambiguous 52.5% / 50.0% / 37.5%, inconsistent 32.5% / 40.0% / 52.5%. Mean calls were 60, 120.5, and 599. The curve is flat. Extra calls do not raise the score.

Six more 1× runs of the same prompt, not part of the table, averaged **45.0%** (standard deviation 2.5). All eight 1× runs of this hash together average **44.0%** (standard deviation 2.9). The table's own 1× cell is the 40.8% above. A re-run of two samples can land on either side of that spread.

Declared kind in the table, pooled over the two runs, so each true kind sums to 40. Exact match is stricter than the label.

| Budget | True unique → unique / ambiguous / inconsistent | True ambiguous | True inconsistent |
|---|---|---|---|
| 1× | 18 / 12 / 10 | 2 / 33 / 5 | 3 / 11 / 26 |
| 3× | 17 / 13 / 10 | 2 / 32 / 6 | 4 / 8 / 28 |
| 10× | 16 / 17 / 7 | 2 / 31 / 7 | 3 / 11 / 26 |

An earlier draft of the prompt, hash `6b74f5d9ac2399cd818ba0654ba09f60db046c2b15529621b64c82d935ebe01f`, averaged 45.3% over six 1× runs. That text is not in the repo. It is not a score for the submitted prompt.

The previous prompt in git, hash `a3663d77e4002520f508230a527b47ce413b2e02decb5e09e228728c81807de9`, scored about 25% at 1×, about 23% at 3×, and about 35% at 10×, one or two runs each. A still older 1× scored 31.67%, and a fixed-letter replay scored 100% without calling Granite. Both of those predate the phrase-list removal. None of these are scores for the submitted prompt.

## Ablations

Same submitted prompt, two runs per cell. Rebuild it with the scorer and key from the candidate package. The default `--timeout-seconds` is 360. A 10× cell is about 600 calls and takes longer than that, so the run below uses 1800:

```bash
python -m src.evaluate \
  --items items.json \
  --key visible_key.json \
  --score-script score.py \
  --timeout-seconds 1800
```

| Configuration | 1× | 3× | 10× |
|---|---|---|---|
| Full system | 40.8% | 40.8% | 41.7% |
| Without prompt examples | 34.2% | 35.0% | 28.3% |
| Without source validation | 45.8% | 44.2% | 45.0% |
| Without extraction review | 45.8% | 40.0% | 44.2% |
| Without sentence decomposition | 48.3% | 47.5% | 41.7% |
| Without minimal conflict search | 26.7% | 30.8% | 30.8% |

Calls: 60 at every 1× cell. At 3× the full system averaged 120.5, and removing the review call dropped that to 61. At 10× the full system used 599, removing the review used 598, and removing the extra line batches used 120.

The conflict search is the component whose removal moves the score on purpose. Without it, inconsistent items score 0 and macro drops by about 10 to 14 points. Removing the worked examples costs about 6 points at 1× and 3× and about 13 at 10×. The 10× gap is outside the spread of the eight full-system 1× runs. The smaller gaps are not.

Source validation does not change `./run`: an accepted letter is already stored with the full original line. The review call and the extra batches do not run at 1×, so those 1× rows are the same code path as the full system and the higher scores are sampling. At 3× and 10×, turning them off does not lower the score. The spare calls are not where the accuracy is.

## Limits

On this prompt the curve does not rise with the budget. 1× and 10× are about the same, which is the shape the brief asks for, at a modest level.

Ambiguous items are the kind this prompt gets right most often at 1×. Inconsistent items are limited by short citations: a right label with one line too few still scores zero. Unique items are still often called ambiguous or inconsistent.

The held-out set has not been run. A menu can only offer a reading built from the names, blocks, stations, and ordinary order words in the line. Other wording can only be answered `X`.

## Key handling

The endpoint key lives in `.env` on the machine that runs `./run`. `.env` is listed in `.gitignore`. Do not commit the key. The answering path never opens `visible_key.json`.

## Design

The job is to get Granite 4.2 8B to turn noisy shift notes into a schedule, without fine-tuning and without a stronger model in the answering path. One-shot, with no harness, Granite scored 0% macro exact match on the 60 visible items. A frontier model, used only as a comparison, scored 67.5% on an earlier pair of runs. The gap is the architecture.

### What I tried first

I asked Granite to emit the constraint object: people, blocks, stations, and typed rules with quotes from the notes. Z3 would solve whatever survived validation. The replies were often not valid JSON. When they were, the types were wrong, "later than" came back as the opposite order, and the person field was sometimes a phrase like "whoever has intake". Validation dropped those rules. Too few rules means more than four schedules, which this task does not allow, or an empty inconsistent answer, which scores zero. Unique items from that setup were around one in ten.

I then let a sentence parser replace the model's rules whenever a line matched a pattern. On the visible 60 that scored perfectly, including when the model reply was thrown away. The assignment checks that answers change when the model output changes, so that overwrite is gone. The parser can propose readings. It does not accept them.

### What runs now

Constraint extraction is model-based. Python prints a menu of readings the named entities can support. Granite picks one letter, or `X`. The parser does not accept a reading by itself.

Python reads the header, because asking Granite to copy the staff list is how fake names got in. For each note line it writes a few readings the entities can support. A directional line gets both orders. A line about last month still gets a reading, so the model can reject it. A car-share, or any line with names but no order word, gets no reading, only `X`. Granite replies with the line number and one letter. A letter that is not on that line's menu, and a missing line, add no rule. An accepted letter is saved with the full original line as the citation, hedge included.

Z3 enumerates. One solution is unique. Two to four are ambiguous, and all of them are returned. Zero is inconsistent: we search for a set of accepted lines that cannot all hold, such that dropping any one of them leaves a set that can. Header facts, such as one person per block, are background and are not part of the citation.

At 3× a review call can change a letter, with a follow-up for option-lines never answered. At 10× spare calls reread lines that have options, never past ten. On the submitted prompt those extra calls do not raise the score.

### Why each piece is there

The header parser keeps names and times exact. The menu exists because Granite is bad at inventing a schema and better, sometimes, at picking between printed sentences. `X` exists so social lines, old rotas, and open questions are the model's decision. The menu is built from the names, times, and stations in the sentence, not from a catalog of note phrases. I removed that catalog. Phrases such as "opens up at" and "whoever drew the" were copied from the visible set, and the held-out notes will not use them.

Validation stops a wrong letter from smuggling in a person or a quote, and the citation is the source line. Z3 checks the schedules and the minimal conflict instead of guessing them. The extra calls exist because temperature is 1.0, and they are capped because 1× counts at least as much as 10×.

Five ablations turn one component off at a time: the worked examples, source-line checks, the review call, the extra line batches, and the minimal-conflict search. `python -m src.evaluate` runs them. The table is above. On the submitted prompt, two runs per cell, removing the conflict search drops inconsistent items to zero and macro by about 10 to 14 points. Removing the worked examples hurts, most clearly at 10× (28.3% against 41.7%). Source validation does nothing here, because every accepted letter already carries the full source line. The review call and the extra batches do not improve the score. I would not defend those two as load-bearing.

### The numbers

The submitted prompt is SHA-256 `e89a46577c8c4eac9d1c1266e4d5300d26a665993c43b38ef2eb390fa0e9624b`. The ablation table, two runs per cell on the 60 visible items, scores 40.8% at 1×, 40.8% at 3×, and 41.7% at 10×. Six further 1× runs of that same file average 45.0%, standard deviation 2.5. All eight 1× runs together average 44.0%, standard deviation 2.9. The six extra runs are not folded into the 3× or 10× cells. Those budgets were only measured inside the table.

A previous draft, hash `6b74f5d9…`, averaged 45.3% over six 1× runs and is not the file in the repo. The prompt before this rewrite, hash `a3663d77…`, was about 25% at 1×. An older 31.67% live run and a 100% fixed-letter replay are earlier still. The replay never called Granite.

### Where it breaks

On the six 1× runs of the submitted prompt I compared each letter with the reading the line should have. Wrong direction was about 3% of real rules. Accepting a distractor that has a menu was about 10% of those lines. Dropping a real rule was about 13%, clustered on hedged sentences and on "between". The prompt before the rewrite accepted about 69% of distractor lines that had a menu. The rewrite cut that, and it also made Granite drop more real rules. Ambiguous items improved. Inconsistent items did not, because a citation one line short scores zero.

I checked the trade on a probe I wrote: 20 lines, names and wording not in the visible set, five repeats. It is not a sample from the benchmark. On the old prompt, false accepts were 20 of 45 and missed rules were 5 of 50. On the submitted prompt, false accepts were 0 of 45 and missed rules were 13 of 50. False acceptance improved and false rejection got worse, which is why the visible score rose by less than the distractor count. "Reckon Priya is on the 13:00 block" and "If I remember right, Omar is not on the 09:00 block" were dropped every time.

Granite cannot select a constraint that was not printed. A person plus a block gets "works" and "does not." An ordinary order word ("before", "sooner", "ahead", "follows") gets both directions even when the sentence is not a visible template. Other order language, or a sentence that never uses the header's names, has nothing to pick. I also tried order options for any line that names two people and nothing else, so "back to back" would have a letter. On the previous prompt draft, six 1× runs with that menu averaged 40.8% and six without it averaged 45.3%, so I took the change out.

If someone else wrote the notes, I would trust the header only while it kept this shape, and the menu only when the sentence names the real people and a block, a station, or an order word. Nicknames, a constraint split across two lines, and other order language leave the model with `X`, so a real rule disappears. That is the cost of refusing free JSON, which for this model usually said something illegal.

The held-out set uses a different phrasing pool and has not been run. With more time I would spend spare calls on the letters that made a rota unsatisfiable, since the flat curve says the current extra calls do not, and I would stop the conflict search from returning a set one statement too short.
