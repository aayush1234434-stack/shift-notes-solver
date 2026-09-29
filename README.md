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

`1x` is one model call per item. `3x` allows up to three, `10x` up to ten. Every item gets at least one call. A call that fails still uses its slot, and the client does not retry it.

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

Three live runs on the 60 visible items, 29 Sep 2026, after the visible-set phrase list was removed from the menu builder. Temperature is 1.0, so each budget is one sample, not an average. The endpoint answered every item. Macro exact match is the mean of the three kind rates.

| Budget | Calls | Macro | Unique | Ambiguous | Inconsistent |
|---|---|---|---|---|---|
| 1× | 60 (1 each) | 25% | 5/20 | 3/20 | 7/20 |
| 3× | 120 (2 each) | 23.33% | 4/20 | 4/20 | 6/20 |
| 10× | 599 (9 or 10 each) | 35% | 6/20 | 6/20 | 9/20 |

At 3× every item used the selection call and the review call. None needed the third call, because every line that had options was answered on the first pass. At 10× the spare calls went back over those lines. One item used 9 calls and the other 59 used 10.

Declared kind, true kind down the rows. The exact column is stricter than the label: a right label with the wrong schedule or the wrong citation is still a miss.

| Budget | True unique → unique / ambiguous / inconsistent | True ambiguous | True inconsistent |
|---|---|---|---|
| 1× | 5 / 2 / 13 | 0 / 4 / 16 | 1 / 1 / 18 |
| 3× | 4 / 1 / 15 | 0 / 4 / 16 | 0 / 2 / 18 |
| 10× | 7 / 2 / 11 | 0 / 9 / 11 | 2 / 3 / 15 |

On the 10× run, one of the seven unique labels had the wrong assignment, and three of the nine ambiguous labels did not list a complete set of real schedules. Those show up in the confusion counts and not in the exact counts.

An earlier live 1× scored 31.67% (unique 9/20, ambiguous 4/20, inconsistent 6/20). A separate replay scored 100% by submitting, for each line, the letter that matches the current reading, with `X` on the rest. Granite was not called for that replay. Both numbers are from before the phrase list was removed. They are not scores for this code.

## Ablations

Each row turns one component off and keeps everything else. `python -m src.evaluate` produces the table. Each cell is one run on the 60 visible items, macro exact match, 29 Sep 2026. The full system was also measured in the runs above, so it has two samples per budget.

| Configuration | 1× | 3× | 10× |
|---|---|---|---|
| Full system (this table's run) | 26.7% | 20.0% | 36.7% |
| Full system (earlier run) | 25.0% | 23.3% | 35.0% |
| Without prompt examples | 25.0% | 23.3% | 31.7% |
| Without source validation | 25.0% | 25.0% | 40.0% |
| Without extraction review | 25.0% | 21.7% | 25.0% |
| Without sentence decomposition | 23.3% | 23.3% | 28.3% |
| Without minimal conflict search | 15.0% | 11.7% | 20.0% |

Calls for the same cells: 60 at 1× everywhere. At 3× the full system used 120, and removing the review call dropped it to 61. At 10× the full system used 599, removing the review call used 598, and removing the extra line batches used 120.

The two full-system samples differ by up to 3.3 points at one budget, and a single item moves macro by 1.7 points. So most rows sit inside the noise. The clear result is the minimal conflict search. Without it the answer for a contradiction is an empty citation, and inconsistent items score 0/20 at every budget. That costs 10 to 17 macro points. The extraction review and the extra line batches show a gap at 10× (25.0% and 28.3% against about 36%), and that gap is the only other one outside the spread of the two full runs. Source validation has no effect on `./run`: every accepted letter already carries the full original line, so removing the check changes nothing. Its higher scores are sampling. The 1× review and batch ablations match the full system by construction, because those calls only exist at 3× and 10×.

## Limits

The curve is two passes at each budget for the full system: 25.0% and 26.7% at 1×, 23.3% and 20.0% at 3×, and 35.0% and 36.7% at 10×. 3× came out a little worse than 1× both times, and 10× came out better both times. With temperature 1.0 the order between 1× and 3× is not settled by two samples.

Most misses are still unique and ambiguous items called inconsistent. On 10× that was 11 of 20 unique items and 11 of 20 ambiguous items. Citations on real inconsistent items are short: mean cited set 2.8 lines at 1×, 2.55 at 3×, and 2.4 at 10×, against a mean core of 3.5 in the visible key. Exact inconsistent answers were 7, 6, and 9 of 20.

A few items produced more than four schedules, so the answer is a truncated ambiguous list: 2 items at 1×, 1 at 3×, 3 at 10×. Those do not score as exact.

The held-out set uses different wording and has not been run. If a sentence does not name header entities in a way the menu understands, Granite can only answer `X`, because it cannot invent a reading that was not printed.

## Key handling

The endpoint key lives in `.env` on the machine that runs `./run`. `.env` is listed in `.gitignore`. Do not commit the key. The answering path never opens `visible_key.json`.

How the pieces were chosen, what was thrown out, and where this breaks on notes from someone else is in [DESIGN.md](DESIGN.md).
