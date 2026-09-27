# Shift Notes Solver

Phase 1 of the AI Researcher take-home: the Granite model client, per-item call
budgets, and local response logging. Puzzle extraction and CSP solving are
planned for later phases. The final `./run <items.json> --budget <1x|3x|10x>
--out <answers.json>` entrypoint is not implemented yet.

## Setup

Python version: **3.12.0** (development and verification).

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` using the exact key and base URL from the provided assignment.
The base URL should end at the API version path, not `/chat/completions`.
The model must remain `ibm-granite/granite-4.2-8b`.

## Synthetic smoke request

```bash
python -m src.smoke --budget 1x
```

This makes exactly one API request asking for a small JSON response, prints
the response and call count, and saves a JSONL ledger under `logs/<run-id>/`.
Each invocation has a new synthetic item ID. This tests connectivity, not
puzzle accuracy. An optional `--env-file /absolute/path/.env` selects another
credential file. Environment variables take precedence over that file.

## Client guarantees

- Fixed model, `temperature=1.0`, `top_p=0.95`, `reasoning.enabled=false`.
- `X-Item-Id` on every request.
- `1x`: exactly one call per item in a successfully completed run.
- `3x`: one to three calls; `10x`: one to ten calls.
- No automatic retries. Failed/uncertain requests consume a call slot.
- Attempt records are persisted before sending. Responses and failures are logged.
- The pipeline must call `assert_budget_compliance(item_ids)` before completing.
- One client per run tracks all item IDs. Existing ledger paths cannot be reused.

The client uses Python's HTTP library directly. The JSON `reasoning` property
is the wire equivalent of `extra_body={"reasoning": {"enabled": False}}` in
the OpenAI SDK. Neither credentials nor request headers are written to logs.
Logs contain model responses and are ignored by Git.

## Offline verification

```bash
python -m unittest discover -s tests -v
```

Tests mock the endpoint: no credentials or real API calls are needed.

Initial verification: five offline tests passed. One synthetic live request
using the supplied credentials reached the endpoint but returned **HTTP 402**.
A successful live response is therefore not verified yet. The failed attempt
was recorded as one call, without retrying. Credentials were read in memory
from the assignment archive and were not copied into the repository.

## Remaining phases

2. Characterize Granite's extraction failures.
3. Define the extraction schema and build the CSP solver.
4. Connect the complete 1x extraction/validation/solving pipeline.
5. Add higher-budget review and repair strategies.
6. Evaluate and reproduce component ablations.
7. Complete the two-page report and submission checks.

## Push Phase 1 yourself

From this repository directory:

```bash
git status --short
git add .gitignore .env.example requirements.txt README.md src tests
git diff --cached --stat
git commit -m "Set up Granite client and per-item call budgets"
git branch -M main
git push -u origin main
```

The real `.env`, `.venv`, and `logs/` are ignored and excluded from the explicit
staging command. Nothing has been committed or pushed automatically.
