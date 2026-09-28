# Phase 6: evaluation and ablations

The evaluation runner calls the repository's `./run` entry point, then scores
its answers with the assignment's unmodified `score.py`. It records the prompt
hashes, input/key/scorer hashes, selected item IDs, exact commands, responses'
run locations, per-cell score, per-case exact rates, confusion matrices, and
the number of completed repeats. Raw response logs, answer JSON, and copies
of the visible key live under ignored `experiments/evaluations/` and
`experiments/runs/`; do not commit them. The evaluation code never supplies
the key to `./run`. Aggregate metrics are recorded below.

Set up `.env` from your own copy of the assignment credentials. Do not commit
it. Use the visible `items.json`, `visible_key.json`, and `score.py` in
`work/visible/`, or point these options at equivalent files. `work/` is
ignored because it contains the visible key.

```sh
.venv/bin/python -m unittest discover -s tests -v

# Full visible set, one run for each budget and each single-component ablation.
.venv/bin/python -m src.evaluate \
  --items work/visible/items.json \
  --key work/visible/visible_key.json \
  --score-script work/visible/score.py \
  --budgets 1x 3x 10x \
  --variants full prompt_examples source_validation extraction_review sentence_decomposition minimal_conflict_search \
  --repeats 1 --shards 6

# Lower-cost, seeded pilot: same three selected items in every configuration.
.venv/bin/python -m src.evaluate \
  --items work/visible/items.json \
  --key work/visible/visible_key.json \
  --score-script work/visible/score.py \
  --per-case 1 --seed 42 --shards 3
```

The runner prints its output directory. Open `ablation_table.md` there for the
scores and confusion matrices, `manifest.json` for exact selection and hashes,
and `results.json` for the individual official-scorer results. Increase
`--repeats` to estimate sampling variability. `--shards` runs independent
subsets in parallel and combines their answers *before* scoring; the model's
call counter and `X-Item-Id` header are still enforced per item. Do not reuse
outputs from one budget as another budget's result.

Each ablation removes only one component via `./run --ablate NAME`:

| Flag | Removed behavior |
|---|---|
| `prompt_examples` | Language-to-rule examples in the extraction prompt. |
| `source_validation` | Original-line quote matching and line-range checks; schema/entity checks remain. |
| `extraction_review` | The second-pass source audit. |
| `sentence_decomposition` | Focused line batches at 3x and small line batches at 10x. |
| `minimal_conflict_search` | Minimal unsatisfiable-core citation search; the inconsistent label can still be returned. |

At 1x, review and sentence decomposition are inactive in the full system, so
their 1x ablations are useful controls rather than expected improvements.
Disabling review or decomposition can also reduce the number of model calls;
any score difference may reflect both the removed component and call count.
The required sampling temperature is 1.0, so one run per cell is not evidence
of a stable causal effect.

## Measured pilot

The bounded pilot selected `B2-001` (ambiguous), `B2-011` (unique), and
`B2-020` (inconsistent), seed 42. This is **three items, one per true class, and one
run per cell**. It is not a score for the full 60-item visible set or the
hidden set. The table below is the official `score.py` macro exact-match
metric; each cell is one scored run on all three selected items.

| Configuration | 1x | 3x | 10x |
|---|---:|---:|---:|
| Full system | 0.0% (n=1; 3 calls) | 0.0% (n=1; 9 calls) | 0.0% (n=1; 9 calls) |
| Without prompt examples | 0.0% (n=1; 3 calls) | 0.0% (n=1; 9 calls) | 0.0% (n=1; 9 calls) |
| Without source validation | 0.0% (n=1; 3 calls) | 0.0% (n=1; 9 calls) | 0.0% (n=1; 16 calls) |
| Without extraction review | 0.0% (n=1; 3 calls) | 0.0% (n=1; 9 calls) | 0.0% (n=1; 9 calls) |
| Without sentence decomposition | 0.0% (n=1; 3 calls) | 0.0% (n=1; 9 calls) | 0.0% (n=1; 9 calls) |
| Without minimal-conflict search | 0.0% (n=1; 3 calls) | 0.0% (n=1; 9 calls) | 0.0% (n=1; 9 calls) |

All 18 cells completed, totaling 133 model calls; no cell score is imputed.
The 60-item source input SHA-256 was
`1f8b0e27dfab48d41d85f1af26adc38c060bb07d846d9d2adb838e8c35aee908`;
the official scorer SHA-256 was
`049a8ad49ce2406c6c089d7b5cc6db70f0b899a97b4da1960bc7e30adef23c1b`.
The full per-cell official-scorer output and confusion matrix are generated
in the ignored evaluation directory by the command above.

The full-system confusion matrix was identical for 1x, 3x, and 10x:

| True case | Declared unique | Declared ambiguous | Declared inconsistent | Unparsed |
|---|---:|---:|---:|---:|
| Unique | 0 | 0 | 1 | 0 |
| Ambiguous | 0 | 0 | 1 | 0 |
| Inconsistent | 0 | 0 | 1 | 0 |

Most ablated cells had that same matrix. The exceptions were 3x without
sentence decomposition, which declared the truly inconsistent item *unique*,
and 10x without source validation, which declared it *ambiguous*. Neither
produced an exact answer. Per-case exact rates were 0.0% in every cell. The
source-validation ablation's 10x call count rose to 16 because it accepted
more extractions and progressed to later calls, but accuracy did not improve.

In the completed full-system baseline runs, 1x scored 0.0% with 3 model calls;
3x scored 0.0% with 9 calls; 10x scored 0.0% with 9 calls. All three runs
declared every pilot item inconsistent, including the truly unique and
ambiguous ones, and none supplied a correct minimal conflict for the truly
inconsistent one. The 10x strategy never reached its extra line batches:
the initial extraction and both replacement attempts failed validation on
each selected item. The apparent 10x call saving is thus a failure mode, not
an efficiency gain.

Observed validation failures include source text not matching the cited line,
unsupported constraint types, unknown people in relationship fields,
malformed JSON, and station constraints assigned to non-station holders.
These are concrete examples from the pilot run logs, not an exhaustive
failure taxonomy. The solver's invented structured-data tests pass; the
measured bottleneck here is getting a complete, valid extraction from Granite.
An `inconsistent` label with an empty conflict list receives no exact-match
credit. A source quote can also match perfectly while its interpreted rule is
semantically wrong; source validation alone cannot detect that.

With just one stochastic run on three items, ablation differences cannot be
interpreted as reliable component effects. A full visible-set run and repeated
samples remain necessary before claiming general accuracy.
