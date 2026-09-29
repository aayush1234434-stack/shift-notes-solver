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

The prompt `./run` sends is `prompts/extract.txt`. `prompts/audit.txt` and `prompts/batch_extract.txt` are an older JSON-edit format. The runner does not use them.

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

One live `1x` run on the 60 visible items, on 29 Sep 2026. Macro exact match **31.67%**.

| Kind | Exact |
|---|---|
| Unique | 9/20 |
| Ambiguous | 4/20 |
| Inconsistent | 6/20 |

`3x` and `10x` have not been run, so there is no live score for them.

A separate replay scored 100% on the same 60 items: 20/20 unique, 20/20 ambiguous, 20/20 inconsistent. That replay fed a fixed letter for each line, the one that matches the current reading, and `X` on the rest. Granite was not called. It is an oracle check that the menus can express this set. It is not model accuracy.

## Limits

Conflict citations are often wrong. All 20 inconsistent items were labeled inconsistent, and only 6 cited a minimal set.

Unique and ambiguous items are often called inconsistent. Nine unique items and fifteen ambiguous items were declared inconsistent on that run. One more ambiguous item was labeled ambiguous but did not list every schedule.

The held-out set uses different wording and has not been run. If a sentence does not name header entities in a way the menu understands, Granite can only answer `X`, because it cannot invent a reading that was not printed.

## Key handling

The endpoint key lives in `.env` on the machine that runs `./run`. `.env` is listed in `.gitignore`. Do not commit the key. The answering path never opens `visible_key.json`.

How the pieces were chosen, what was thrown out, and where this breaks on notes from someone else is in [DESIGN.md](DESIGN.md).
