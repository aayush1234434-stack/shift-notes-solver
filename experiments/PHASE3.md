# Phase 3: structured schema and exact solver

Install the updated pinned requirements in your virtual environment:

```bash
python -m pip install -r requirements.txt
```

The extraction contract is `schemas/extraction.schema.json`; `src/schema.py`
enforces its shape plus cross-field requirements: equal people/block counts,
equal station/holder counts, valid entity references and exact sources.
Block order is the chronological order from the header. Strings are preserved.
`ignored_lines` is optional for compatibility with Phase 2 output.

Source lines count nonempty original lines starting at 1. Providing original
notes enables exact full-line source verification; without notes only source
shape and consistency can be validated. Several extracted rules from the same
sentence share its line number and source. Invalid rules cause explicit errors.

## Try the three invented examples

```bash
python -m src.solve_structured --input examples/unique.json --notes examples/unique.txt
python -m src.solve_structured --input examples/ambiguous.json --notes examples/ambiguous.txt
python -m src.solve_structured --input examples/inconsistent.json --notes examples/inconsistent.txt
```

Add `--out /path/to/result.json` to save a single-item result. This utility
does not read raw assignment items or call a model. It is not the final `./run`
entrypoint; Phase 4 will connect model extraction to this solving layer.

## Supported rules

| Type | Meaning |
|---|---|
| fixed_block / not_block | Person is/is not at the specified block |
| fixed_station / not_station | Station holder is/is not at the specified station |
| before / after | Strict order, not necessarily adjacent |
| immediately_before / immediately_after | Consecutive positions in the block list |
| adjacent / not_adjacent | Consecutive/not consecutive in either order |
| between | Strictly between two people in either orientation, not necessarily adjacent |

## Solver behavior

The Z3 compiler gives every person a bounded block variable and every station
holder a bounded station variable. All blocks are distinct; all assigned
stations are distinct. People without stations have no station variable.

Enumeration blocks each complete model across both time and station variables.
Solutions are sorted for repeatable output. One solution yields `unique`; two
to four yield `ambiguous`, containing every complete assignment. More than four
raises `ExtractionIncompleteError`: the solver never truncates or invents an
assignment-compatible verdict. `all_solutions()` remains available for inspecting
the complete set when diagnosing extraction.

Zero solutions triggers conflict shrinking. Header constraints remain active;
all rules from one original statement are removed together. The returned set
is contradictory and removing any one cited statement restores satisfiability.
It is deletion-minimal, not necessarily the smallest conflicting set. Citations
come directly from stored source strings. This establishes logical correctness
relative to extraction; it does not prove the model interpreted English correctly.

## Python interface

```python
from src.schema import parse_problem
from src.solver import ScheduleSolver, solve_problem

problem = parse_problem(extraction_json, raw_text=original_notes)
answer = solve_problem(problem)
solver = ScheduleSolver(problem)
all_assignments = solver.all_solutions()
```

Offline verification:

```bash
python -m unittest discover -s tests -v
```

Z3 references: [official Solver API](https://z3prover.github.io/api/html/classz3py_1_1_solver.html).

## Verification results

On Python 3.12.0 with Z3 4.15.3.0, all 21 project tests passed, including 12 new
Phase 3 tests. All three example CLI commands returned the expected result.
Checks include independent permutation comparisons for every supported rule,
complete 720-assignment enumeration, station-only ambiguity, exact source
matching, invalid schema rejection, and statement-level minimal conflicts.
No model calls were needed for Phase 3 verification.
