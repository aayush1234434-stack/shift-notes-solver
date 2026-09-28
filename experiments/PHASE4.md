# Phase 4: complete 1x pipeline

Activate your environment, install requirements and configure the private `.env`
with the assignment endpoint and credentials. The required launcher is:

```bash
./run /path/to/items.json --budget 1x --out answers.json
```

`--env-file /path/to/candidate_package/.env` is optional. Environment variables
take precedence. `3x` and `10x` are not implemented until Phase 5 and are rejected
rather than silently using a different strategy.

## Visible-set evaluation

```bash
python -m src.prepare_visible "/path/to/AI Researcher - R1 Assignment.zip"
./run work/visible/items.json --budget 1x --out answers-1x.json
python work/visible/score.py work/visible/visible_key.json answers-1x.json work/visible/items.json
```

Run the scorer only after `./run` exits successfully. A failed run does not
publish final answers; an older file at the output path is left unchanged.
`prepare_visible` copies only the items, key and official scorer. The answering
pipeline never reads the key. Evaluation files live in the ignored `work/` folder.

## Behavior

One Granite call per item extracts header lists and source-linked typed rules.
The client enforces sampling settings, reasoning disabled, `X-Item-Id` and no
automatic retries. Validation checks strict JSON, unique object keys, schema,
header counts against available metadata, entities and exact original sources.
An exact outer JSON code fence may be removed; facts and strings are not rewritten.
Entity occurrence checks cannot independently prove that the header was interpreted
correctly. Original notes remain the authority during semantic review.

Z3 enumerates complete schedules and finds statement-level minimal conflicts.
Successful outputs preserve names, blocks, stations and source strings exactly.
Every item has exactly one request before any final answer is published.

At 1x there is no call available for extraction repair. These explicit failure
policies keep a run evaluable without claiming extraction was correct:

- Invalid extraction: emit `inconsistent` with empty `conflicts`; logs mark it
  `unresolved`. This is an abstention encoded in an allowed shape and earns zero
  exact-match credit. It is not a verified contradiction.
- More than four solutions: emit the first four complete solutions, mark the
  item `partial_extraction`, and retain the diagnostic. They satisfy the extracted
  rules, but may not satisfy the original text. The system does not claim this
  is the complete solution set. It may receive diagnostic partial credit only.
- Endpoint failure: stop without publishing a partial final file. Do not retry.

## Evidence

`experiments/runs/pipeline-<run-id>/` records the manifest, prompts, raw responses,
validation errors, answers, call ledger, counts and run summary. Credentials are
not logged. The README is unchanged.

## Initial verification and visible-set attempt

On 2026-09-27, all 30 offline project tests passed. These include an invented
three-item complete pipeline, changed model output changing the schedule,
invalid extraction without retries, atomic publication, and endpoint failure
without overwriting previous answers. The executable launcher was checked from
outside the repository. These are engineering checks, not a benchmark score.

The actual command `./run work/visible/items.json --budget 1x --out answers-1x.json`
was attempted with the supplied credentials, loaded only in memory. The input
contained 60 items. The first request, for B2-000, returned HTTP 402. One request
was attempted, no model response was received, and the run stopped without
publishing answers. The official scorer was not run on nonexistent output.

Evidence: `experiments/runs/pipeline-f3f7d3737b9844e6b532971b26eb3201/` (ignored).
Measured visible-set score: **unavailable pending a usable endpoint**.
