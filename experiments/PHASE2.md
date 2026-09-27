# Phase 2: model characterization

This pilot evaluates extraction, before the CSP solver exists. Defaults select
two items from each true case (six total), with a fixed seed. The visible key is
used only to select a balanced sample; answers and labels are never sent to the
model. All responses come from the required Granite model.

Activate the Phase 1 virtual environment and configure your private `.env`.
Then run:

```bash
python -m src.characterize --package-zip "/path/to/AI Researcher - R1 Assignment.zip"
```

Alternatively use extracted data:

```bash
python -m src.characterize --items /path/to/items.json --key /path/to/visible_key.json
```

Add `--prepare-only` to inspect the selected items and exact prompts without
sending requests. Add `--per-case 3 --seed 42` for a nine-item pilot. Each run
has a new directory; rerunning makes new calls, rather than retrying old ones.
Calls use the actual item IDs and the 1x budget. The runner stops at the first
request failure to avoid consuming calls on a broken endpoint.

## Saved evidence

Each `experiments/runs/<run-id>/` contains:

- `manifest.json`: sample labels, settings, seed, prompt and dataset hashes.
- `requests.json`: exact prompts, numbered original lines, and item IDs.
- `records.json`: raw responses, structural diagnostics, and request errors.
- `calls.jsonl` and `call_counts.json`: attempted calls and response metadata.
- `review.json`: a worksheet for comparing extracted rules to their sources.
- `failure_list.md`: diagnostics and manually confirmed failures.

Run directories are ignored by Git. Do not publish raw experiment files until
you have checked that sharing the assignment data is permitted.

## Manual semantic review

Automatic checks detect malformed JSON, invalid fields, unknown entities and
source mismatches. They cannot determine that a real sentence was interpreted
correctly. An unaccounted line is a review candidate, not a proven omission.

For each successful response, compare `records.json` with the corresponding
numbered lines in `review.json`. Check every line, including those marked ignored,
and verify the header lists. In `review.json`, set an item's status to `reviewed`
and add concrete findings, for example:

```json
{
  "tag": "ordering_error",
  "line": 5,
  "observation": "The source says before, but extraction says immediately_before."
}
```

Use only tags listed in `allowed_tags`. Leave `findings` empty if you checked
the response and found no semantic errors. Then rebuild the short failure list:

```bash
python -m src.characterize --summarize experiments/runs/<run-id>
```

The report distinguishes automatic diagnostics from confirmed semantic failures
and reports how many responses were actually reviewed. It does not claim puzzle
accuracy or silently turn endpoint errors into model failures.
