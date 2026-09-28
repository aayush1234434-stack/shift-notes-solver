# Extraction repair

The first pilot failed before solving: Granite copied header text into
constraints, paraphrased source citations, used `whoever has intake/packing`
as a person, emitted unsupported `not_between`, classified the same line as
both ignored and extracted, and treated historical or hypothetical notes as
current rules.

The extraction path now:

- parses the standardized rota header deterministically and sends its lists as
  context instead of asking Granite to reproduce them;
- supports `station_before_person` and `station_after_person`, so station
  holders can be resolved by the solver rather than represented as fake people;
- accepts a verifiable excerpt from a numbered line, then canonicalizes it to
  the original full line for final citations;
- removes header-line constraints and reconciles `ignored_lines` in salvage
  mode;
- normalizes explicit “no block between … in that order” and clear station
  ordering statements;
- drops malformed, non-current, unsupported, or unknown-entity rules while
  retaining independently valid rules and recording diagnostics;
- uses higher-budget calls as line batches after a failed page extraction,
  instead of spending all calls on repeated invalid full-page JSON.

The solver remains unchanged in its scheduling semantics. All model-derived
rules still pass the typed schema and source-evidence checks before entering
Z3.

Verification:

```sh
.venv/bin/python -m unittest discover -s tests -q
```

The suite currently passes 48 tests. A six-item 1x pilot run after the first
repair scored 33.33% macro exact match; it selected two items per class and is
not directly comparable to the earlier three-item pilot. A replay of the
original three recorded responses through the repaired validator produced
valid unique, ambiguous, and inconsistent classifications, but this replay is
diagnostic rather than an independent model run.
