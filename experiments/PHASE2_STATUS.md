# Phase 2 initial status

The characterization runner is implemented. Nine offline tests passed, including
balanced sampling, source/entity diagnostics, call limits and no automatic retries.
The project README has not been modified for Phase 2.

## Live pilot: 2026-09-27

- Planned sample: six visible items, two unique, two ambiguous, two inconsistent.
- Seed: 42; one planned pass, one call per item.
- Selected IDs, in execution order: see the run manifest.
- Attempted item: B2-029.
- Result: HTTP 402 from the provided endpoint.
- Model responses received: zero.
- Calls attempted: one; no retry; remaining five items not attempted.

Local evidence is stored in the ignored run directory
`experiments/runs/b5a0ceba07954c198040fd913b6cfdc8/`.
No credentials were saved to the repository.

## Failure list

No model extraction failures have been established yet. The endpoint failure is
an operational blocker, not evidence about Granite's language understanding.
Missed constraints, filler mistakes, negation, ordering, betweenness, changed
entities and hedging errors all remain unmeasured.

When the endpoint is usable, rerun the pilot, review successful responses using
`review.json`, and regenerate the failure list. See PHASE2.md for commands.
