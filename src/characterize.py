"""Phase 2: balanced pilot, immutable request records, and manual failure review.

Run with --help for input options. This is an extraction experiment, not a solver.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZipFile

from dotenv import load_dotenv

from .model_client import GraniteClient, MODEL, ModelConfig, ModelRequestError

ROOT = Path(__file__).resolve().parents[1]
CASES = ("unique", "ambiguous", "inconsistent")
FIELDS = {
    "fixed_block": ("person", "block"), "not_block": ("person", "block"),
    "fixed_station": ("person", "station"), "not_station": ("person", "station"),
    **{kind: ("person", "other_person") for kind in (
        "before", "after", "immediately_before", "immediately_after",
        "adjacent", "not_adjacent")},
    "between": ("person", "other_person", "third_person"),
}
REVIEW_TAGS = (
    "missed_constraint", "filler_as_constraint", "negation_error",
    "ordering_error", "between_error", "changed_entity", "hedge_error",
    "header_error", "other",
)


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def select_sample(items: list[dict], key: dict, per_case: int, seed: int) -> list[dict]:
    if per_case < 1:
        raise ValueError("--per-case must be positive")
    ids = [item["id"] for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate item IDs")
    rng = random.Random(seed)
    selected = []
    for case in CASES:
        group = sorted((item for item in items if key[item["id"]]["case"] == case),
                       key=lambda item: item["id"])
        if len(group) < per_case:
            raise ValueError(f"Not enough {case} items for this sample")
        selected.extend(rng.sample(group, per_case))
    rng.shuffle(selected)
    return selected


def inspect_response(raw: str, lines: list[str]) -> list[dict]:
    """Structural checks only; semantic errors require manual source comparison."""
    issues = []

    def flag(kind, detail):
        issues.append({"kind": kind, "detail": detail})

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return [{"kind": "malformed_json", "detail": "Response is not strict JSON"}]
    if not isinstance(data, dict):
        return [{"kind": "invalid_schema", "detail": "Expected a JSON object"}]
    groups = {}
    for name in ("people", "blocks", "stations", "station_holders"):
        value = data.get(name)
        if not isinstance(value, list) or not value or any(not isinstance(x, str) for x in value):
            flag("invalid_schema", f"{name} must be a nonempty list of strings")
            groups[name] = []
        else:
            groups[name] = value
            if len(value) != len(set(value)):
                flag("invalid_schema", f"Duplicate values in {name}")
            for entity in value:
                if not entity or entity not in "\n".join(lines):
                    flag("entity_not_in_text", f"{name}: {entity!r}")
    for person in groups["station_holders"]:
        if person not in groups["people"]:
            flag("unknown_entity", f"Station holder not in people: {person!r}")
    constraints = data.get("constraints")
    ignored = data.get("ignored_lines")
    if not isinstance(constraints, list):
        flag("invalid_schema", "constraints must be a list")
        constraints = []
    if not isinstance(ignored, list):
        flag("invalid_schema", "ignored_lines must be a list")
        ignored = []
    extracted_lines = set()
    for index, rule in enumerate(constraints):
        if not isinstance(rule, dict):
            flag("invalid_schema", f"Constraint {index} is not an object")
            continue
        kind = rule.get("type")
        if not isinstance(kind, str) or kind not in FIELDS:
            flag("unsupported_type", f"Constraint {index}: {kind!r}")
            continue
        for field in FIELDS[kind]:
            group = "blocks" if field == "block" else "stations" if field == "station" else "people"
            if rule.get(field) not in groups[group]:
                flag("unknown_entity", f"Constraint {index}: invalid {field}")
        number = rule.get("source_line")
        if type(number) is not int or not 1 <= number <= len(lines):
            flag("invalid_source", f"Constraint {index}: invalid source_line")
        else:
            extracted_lines.add(number)
            if rule.get("source") != lines[number - 1]:
                flag("source_mismatch", f"Constraint {index}: source differs from line {number}")
    ignored_set = set()
    for number in ignored:
        if type(number) is not int or not 1 <= number <= len(lines):
            flag("invalid_source", "ignored_lines contains an invalid line number")
        else:
            ignored_set.add(number)
    for number in sorted(extracted_lines & ignored_set):
        flag("conflicting_classification", f"Line {number} is both extracted and ignored")
    # Headers are deliberately not inferred by a symbolic parser here. Unaccounted
    # lines are review candidates, not confirmed missed constraints.
    for number in sorted(set(range(1, len(lines) + 1)) - extracted_lines - ignored_set):
        flag("unaccounted_line", f"Review line {number}; it may be a header or a missed rule")
    return issues


def summarize(run_dir: Path) -> None:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    records = json.loads((run_dir / "records.json").read_text(encoding="utf-8"))
    review = json.loads((run_dir / "review.json").read_text(encoding="utf-8"))
    automatic = Counter(issue["kind"] for record in records for issue in record["issues"])
    observed = Counter()
    notes = []
    reviewed = 0
    returned_ids = {record["item_id"] for record in records if record["response"] is not None}
    for entry in review["items"]:
        if entry["status"] not in {"pending", "reviewed"}:
            raise ValueError("Review status must be pending or reviewed")
        if entry["status"] != "reviewed":
            continue
        if entry["item_id"] not in returned_ids:
            raise ValueError("Cannot mark an item reviewed without a model response")
        reviewed += 1
        for finding in entry["findings"]:
            tag = finding["tag"]
            if tag not in REVIEW_TAGS or not finding.get("observation", "").strip():
                raise ValueError("Each finding needs an allowed tag and a concrete observation")
            if type(finding.get("line")) is not int or not 1 <= finding["line"] <= len(entry["lines"]):
                raise ValueError("Each finding needs a valid source line number")
            observed[tag] += 1
            notes.append(f"- {entry['item_id']}, line {finding['line']}, {tag}: {finding['observation']}")
    successes = len(returned_ids)
    text = ["# Phase 2 characterization", "",
            f"Selected: {len(manifest['selected'])} items; one run, seed {manifest['seed']}.",
            f"Attempted: {len(records)}; responses: {successes}; manually reviewed: {reviewed}.",
            f"Model: {MODEL}; temperature=1.0; top_p=0.95; reasoning disabled.", "",
            "## Automatic diagnostics", "",
            "These checks concern structure and provenance. They do not prove semantic correctness.",
            "Unaccounted lines may be headers rather than missed constraints.", ""]
    text.extend(f"- {kind}: {count}" for kind, count in sorted(automatic.items()))
    if not automatic:
        text.append("No automatic findings available." if not successes else "No structural findings.")
    text.extend(["", "## Observed semantic failures", ""])
    text.extend(notes or ["No reviewed semantic findings yet; no semantic accuracy claim is made."])
    text.extend(["", "## Architecture implications", ""])
    implications = {
        "missed_constraint": "Track sentence coverage and review unmapped statements.",
        "filler_as_constraint": "Separate definite scheduling statements from unrelated details.",
        "negation_error": "Validate excluded block/station extraction with focused examples.",
        "ordering_error": "Distinguish before/after from immediate adjacency.",
        "between_error": "Represent both possible orientations of betweenness.",
        "changed_entity": "Preserve source spellings and validate entity references.",
        "hedge_error": "Treat hedged constraints at full force.",
        "header_error": "Check header extraction before compiling constraints.",
        "other": "Review the cited observation before changing architecture.",
    }
    text.extend(f"- {implications[tag]} ({count} reviewed finding(s))" for tag, count in sorted(observed.items()))
    if not observed:
        text.append("Pending successful responses and manual review.")
    errors = [record["error"] for record in records if record["error"]]
    if errors:
        text.extend(["", "## Endpoint failures", "", *[f"- {error}" for error in errors],
                     "", "Stopped after the first request failure; no automatic retry."])
    (run_dir / "failure_list.md").write_text("\n".join(text) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-zip", type=Path)
    parser.add_argument("--items", type=Path)
    parser.add_argument("--key", type=Path)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--per-case", type=int, default=2)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--prepare-only", action="store_true", help="Create records without API calls")
    parser.add_argument("--summarize", type=Path, help="Rebuild report after editing review.json")
    args = parser.parse_args()
    if args.summarize:
        summarize(args.summarize)
        print(args.summarize / "failure_list.md")
        return 0
    if args.package_zip:
        if args.items or args.key:
            parser.error("Choose --package-zip OR --items and --key")
        with ZipFile(args.package_zip) as archive:
            # Never read or extract .env from the package in this CLI.
            items = json.loads(archive.read("candidate_package/items.json"))
            key = json.loads(archive.read("candidate_package/visible_key.json"))
    elif args.items and args.key:
        items = json.loads(args.items.read_text(encoding="utf-8"))
        key = json.loads(args.key.read_text(encoding="utf-8"))
    else:
        parser.error("Supply --package-zip or both --items and --key")
    selected = select_sample(items, key, args.per_case, args.seed)
    prompt = (ROOT / "prompts" / "characterize.txt").read_text(encoding="utf-8")
    run_dir = ROOT / "experiments" / "runs" / uuid.uuid4().hex
    run_dir.mkdir(parents=True)
    requests, review = [], []
    for item in selected:
        lines = [line for line in item["text"].splitlines() if line.strip()]
        messages = [{"role": "system", "content": prompt},
                    {"role": "user", "content": "\n".join(
                        f"{number}: {line}" for number, line in enumerate(lines, 1))}]
        requests.append({"item_id": item["id"], "lines": lines, "messages": messages})
        review.append({"item_id": item["id"], "status": "pending", "lines": lines, "findings": []})
    write_json(run_dir / "manifest.json", {
        "created": datetime.now(timezone.utc).isoformat(), "seed": args.seed,
        "per_case": args.per_case, "model": MODEL, "temperature": 1.0,
        "top_p": 0.95, "reasoning": {"enabled": False}, "budget": "1x",
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "dataset_sha256": hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest(),
        "selected": [{"id": item["id"], "case": key[item["id"]]["case"]} for item in selected],
        "note": "Key labels select a balanced pilot only; no key content is sent to Granite."})
    write_json(run_dir / "requests.json", requests)
    write_json(run_dir / "review.json", {"allowed_tags": REVIEW_TAGS, "items": review})
    records = []
    write_json(run_dir / "records.json", records)
    if args.prepare_only:
        summarize(run_dir)
        print(f"Prepared without model calls: {run_dir}")
        return 0
    load_dotenv(args.env_file, override=False)
    client = GraniteClient(ModelConfig.from_env(), "1x", run_dir / "calls.jsonl")
    failed = False
    for request in requests:
        record = {"item_id": request["item_id"], "response": None, "error": None, "issues": []}
        try:
            record["response"] = client.complete(request["item_id"], request["messages"])
            record["issues"] = inspect_response(record["response"], request["lines"])
        except ModelRequestError as exc:
            record["error"] = str(exc)
            failed = True
        records.append(record)
        write_json(run_dir / "records.json", records)
        write_json(run_dir / "call_counts.json", client.call_counts)
        summarize(run_dir)
        print(f"{request['item_id']}: {'request failed' if failed else 'response recorded'}")
        if failed:
            break
    if not failed:
        client.assert_budget_compliance([item["id"] for item in selected])
    print(f"Results: {run_dir}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
