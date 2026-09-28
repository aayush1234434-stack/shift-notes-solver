# Phase 5: higher-budget extraction strategies

The existing launcher now accepts all three budgets:

```bash
./run work/visible/items.json --budget 1x --out answers-1x.json
./run work/visible/items.json --budget 3x --out answers-3x.json
./run work/visible/items.json --budget 10x --out answers-10x.json
```

Every item receives at least one Granite call. The model client enforces an
individual maximum of 1, 3, or 10 calls, sets the required sampling parameters,
disables reasoning, and sends `X-Item-Id` on every request. Failed requests
consume a slot and are never retried automatically.

## Strategies

| Budget | Calls per item | Purpose |
|---|---|---|
| 1x | Exactly 1 | Original full extraction and solver |
| 3x | 1–3 | Full extraction; audit every valid extraction; verify changed lines or investigate an anomalous solution count |
| 10x | 1–10 | Full extraction; audit; then independently extract all non-header lines in small batches |

If a full extraction is invalid, higher budgets spend up to two remaining calls
on full replacement before attempting an audit or batch extraction. That is the
only case where a full-page extraction is repeated. An audit is requested even
when the original constraints yield one to four schedules: such a count does
not prove the language was interpreted correctly.

The audit returns line replacements. A replacement removes all rules from a
source line and supplies a complete new set, or an empty set if that line was
filler. At 3x, a third call re-extracts the changed lines; if the audit finds
no changes but the schedule count is zero or above four, the third call reviews
all non-header lines. At 10x, remaining calls partition all non-header lines
into at most eight batches. Fewer calls are permitted when fewer batches exist.

Every audit or batch response must quote the exact original source line,
reference only allowed entities, and conform to the typed schema. The complete
merged extraction is revalidated before it replaces the previous candidate.
Malformed or invalid corrections are logged and ignored. The CSP solver runs
again on the accepted candidate. This protects structure and provenance; it
cannot prove that a semantically plausible model interpretation is correct.

All prompts, raw responses, rejected corrections, per-item call counts and
final decisions are retained in ignored `experiments/runs/pipeline-<run-id>/`.
Only the final answers JSON goes to the requested `--out` path. The answering
pipeline never reads `visible_key.json`.

## Verification

All 35 offline project tests pass. New tests cover repairing a wrong answer
that initially had one solution, auditing despite a valid solution count,
recovering an invalid first extraction, rejecting hallucinated sources, and
covering all lines with exactly ten calls. The 1x tests still pass. The launcher
accepts `1x`, `3x` and `10x` from the command line.

These tests use invented notes and fake model responses. They verify the
control flow and budget limits, not model accuracy. Earlier live requests to
the provided endpoint returned HTTP 402; no higher-budget benchmark or score
has been measured yet. When the endpoint is usable, run each budget on the
visible set, score with the provided `score.py`, inspect per-case rates, and
record run counts before claiming an improvement.
