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

Same submitted prompt, two runs per cell. `python -m src.evaluate` rebuilds the table.

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

How the pieces were chosen, what was thrown out, and where this breaks on notes from someone else is in [DESIGN.md](DESIGN.md).
