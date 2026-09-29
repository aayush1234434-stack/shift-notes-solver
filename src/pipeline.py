"""Budgeted extraction, validation, Z3 solving, and final answer JSON."""

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from .answer_validation import AnswerValidationError, validate_answer_for_problem
from .extractor import build_messages, decode_extraction, model_cited_lines
from .selection import (batch_messages, menus_for_item, parse_choices,
                        problem_from_choices, review_messages, selected_lines)
from .model_client import BUDGETS, GraniteClient, MODEL, ModelConfig, ModelRequestError
from .output_writer import atomic_json, write_answers
from .schema import SchemaError
from .solver import ExtractionIncompleteError, ScheduleSolver, SolverError

ROOT = Path(__file__).resolve().parents[1]
ABLATIONS = ("prompt_examples", "source_validation", "extraction_review",
             "sentence_decomposition", "minimal_conflict_search")


def validate_items(items) -> None:
    if not isinstance(items, list) or not items:
        raise ValueError("Input must be a nonempty JSON array")
    ids = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each item must be an object")
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id or item_id.strip() != item_id or any(
            ord(char) < 32 or ord(char) > 126 for char in item_id
        ):
            raise ValueError("Each item needs a printable, nonempty ASCII ID")
        if not isinstance(item.get("text"), str) or not item["text"].strip():
            raise ValueError(f"{item_id}: missing notes")
        for field in ("n_staff", "n_stations"):
            if field in item and (type(item[field]) is not int or item[field] < 0):
                raise ValueError(f"{item_id}: invalid {field}")
        ids.append(item_id)
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate item IDs")


def answer_from_problem(problem, minimal_conflict_search: bool = True,
                        prefer_lines=None) -> tuple[dict, str, str | None]:
    try:
        solver = ScheduleSolver(problem)
        if not minimal_conflict_search and not solver.is_satisfiable():
            return {"case": "inconsistent", "conflicts": []}, "core_search_ablated", None
        try:
            answer = solver.result(prefer_lines)
        except ExtractionIncompleteError as exc:
            # No second call exists at 1x. Return a documented partial attempt
            # rather than inventing rules. All emitted schedules satisfy extraction.
            answer = {"case": "ambiguous", "assignments": solver.all_solutions()[:4]}
            validate_answer_for_problem(answer, problem, allow_partial=True)
            return answer, "partial_extraction", str(exc)
        validate_answer_for_problem(answer, problem)
        return answer, "solved_from_extraction", None
    except (SchemaError, SolverError, AnswerValidationError) as exc:
        # There is no abstention shape in the assignment. An empty conflicting
        # set earns zero credit; logs explicitly label this as unresolved, not
        # as a certified contradiction. Never fabricate names or citations.
        return {"case": "inconsistent", "conflicts": []}, "unresolved", str(exc)


def answer_from_response(response: str, item: dict, source_validation: bool = True,
                         minimal_conflict_search: bool = True) -> tuple[dict, str, str | None]:
    try:
        return answer_from_problem(decode_extraction(response, item, source_validation),
                                   minimal_conflict_search, model_cited_lines(response))
    except SchemaError as exc:
        return {"case": "inconsistent", "conflicts": []}, "unresolved", str(exc)


def solve_count(problem) -> int:
    return len(ScheduleSolver(problem).all_solutions())


def _absorb(choices: dict[int, str], response: str, allowed: set[int] | None = None) -> None:
    """Apply on-menu letters. An off-menu letter or a line outside the batch is ignored."""
    for number, key in parse_choices(response).items():
        if allowed is not None and number not in allowed:
            continue
        choices[number] = key


def solve_item(item: dict, client: GraniteClient, record: dict, records: list,
               run_dir: Path, budget: str, ablate: str | None = None) -> tuple[dict, str, str | None]:
    """One selection call, then optional review and line batches within the cap.

    A constraint reaches Z3 only when the model picks a menu letter for its line.
    Missing lines and X add no rule. The structural menu is not applied on its own.
    """
    item_id = item["id"]
    cap = BUDGETS[budget]
    issues = []
    source_validation = ablate != "source_validation"
    prompt_examples = ablate != "prompt_examples"
    minimal_conflict_search = ablate != "minimal_conflict_search"
    menus = menus_for_item(item)
    choices: dict[int, str] = {}

    def call(kind: str, messages: list[dict[str, str]]) -> str:
        if len(record["attempts"]) >= cap:
            raise RuntimeError(f"{item_id}: internal call budget exceeded")
        attempt = {"kind": kind, "messages": messages, "response": None, "error": None}
        record["attempts"].append(attempt)
        atomic_json(run_dir / "records.json", records)
        try:
            response = client.complete(item_id, messages)
        except ModelRequestError as exc:
            attempt["error"] = str(exc)
            record.update(status="endpoint_failure", error=str(exc))
            atomic_json(run_dir / "records.json", records)
            atomic_json(run_dir / "call_counts.json", client.call_counts)
            raise
        attempt["response"] = response
        atomic_json(run_dir / "records.json", records)
        atomic_json(run_dir / "call_counts.json", client.call_counts)
        return response

    def optional_call(kind: str, messages: list[dict[str, str]]) -> str | None:
        try:
            return call(kind, messages)
        except ModelRequestError as exc:
            issues.append(str(exc))
            return None

    messages = build_messages(item, prompt_examples)
    record["messages"] = messages
    response = call("selection", messages)
    record["response"] = response
    _absorb(choices, response)

    if budget != "1x" and ablate != "extraction_review" and len(record["attempts"]) < cap:
        raw = optional_call("selection_review", review_messages(item, choices, prompt_examples))
        if raw is not None:
            _absorb(choices, raw)

    if budget == "3x" and ablate != "sentence_decomposition" and len(record["attempts"]) < cap:
        missing = [number for number, options in menus.items()
                   if options and number not in choices]
        if missing:
            raw = optional_call("focused_reselect",
                                batch_messages(item, missing, prompt_examples))
            if raw is not None:
                _absorb(choices, raw, set(missing))

    if budget == "10x" and ablate != "sentence_decomposition":
        numbered = [number for number, options in menus.items() if options]
        remaining = cap - len(record["attempts"])
        for target in _option_batches(numbered, remaining):
            raw = optional_call("batch_reselect", batch_messages(item, target, prompt_examples))
            if raw is not None:
                _absorb(choices, raw, set(target))

    try:
        problem = problem_from_choices(item, choices, source_validation)
    except SchemaError as exc:
        issues.append(str(exc))
        return {"case": "inconsistent", "conflicts": []}, "unresolved", "; ".join(issues)
    cited = selected_lines(choices, menus)
    answer, status, error = answer_from_problem(problem, minimal_conflict_search, cited)
    return answer, status, "; ".join(filter(None, [*issues, error])) or None


def _option_batches(numbers: list[int], max_batches: int) -> list[list[int]]:
    count = min(max_batches, len(numbers))
    if count <= 0:
        return []
    return [numbers[index::count] for index in range(count)]


def run_pipeline(items: list[dict], client: GraniteClient, run_dir: Path, output: Path,
                 budget: str | None = None, ablate: str | None = None) -> dict:
    validate_items(items)
    budget = budget or getattr(client, "budget", "1x")
    if budget not in BUDGETS:
        raise ValueError(f"Unsupported budget: {budget}")
    if ablate is not None and ablate not in ABLATIONS:
        raise ValueError(f"Unsupported ablation: {ablate}")
    answers = {}
    records = []
    for position, item in enumerate(items, 1):
        record = {"item_id": item["id"], "messages": None, "response": None,
                  "attempts": [], "status": "request_pending", "error": None, "answer": None}
        records.append(record)
        atomic_json(run_dir / "records.json", records)
        answer, status, error = solve_item(item, client, record, records, run_dir, budget, ablate)
        answers[item["id"]] = answer
        record.update(status=status, error=error, answer=answer)
        atomic_json(run_dir / "records.json", records)
        atomic_json(run_dir / "call_counts.json", client.call_counts)
        print(f"[{position}/{len(items)}] {item['id']}: {status}", file=sys.stderr)
    client.assert_budget_compliance([item["id"] for item in items])
    write_answers(output, answers, [item["id"] for item in items])
    return {"items": len(items), "status_counts": {
        status: sum(record["status"] == status for record in records)
        for status in sorted({record["status"] for record in records})},
        "call_counts": client.call_counts}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("items", type=Path)
    parser.add_argument("--budget", required=True, choices=list(BUDGETS))
    parser.add_argument("--ablate", choices=ABLATIONS,
                        help="Disable one component for a reproducible ablation")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    run_dir = None
    try:
        if args.items.resolve() == args.out.resolve():
            raise ValueError("Input and output paths must differ")
        items = json.loads(args.items.read_text(encoding="utf-8"))
        validate_items(items)
        load_dotenv(args.env_file, override=False)
        config = ModelConfig.from_env()
        run_dir = ROOT / "experiments" / "runs" / ("pipeline-" + uuid.uuid4().hex)
        run_dir.mkdir(parents=True)
        prompt_hashes = {
            name: hashlib.sha256((ROOT / "prompts" / name).read_bytes()).hexdigest()
            for name in ("extract.txt", "audit.txt", "batch_extract.txt")
        }
        atomic_json(run_dir / "manifest.json", {
            "created": datetime.now(timezone.utc).isoformat(), "budget": args.budget,
            "ablate": args.ablate,
            "model": MODEL, "temperature": 1.0, "top_p": 0.95,
            "reasoning": {"enabled": False}, "items": len(items),
            "input_sha256": hashlib.sha256(args.items.read_bytes()).hexdigest(),
            "prompt_sha256": prompt_hashes,
            "output": str(args.out.resolve()), "item_ids": [item["id"] for item in items]})
        client = GraniteClient(config, args.budget, run_dir / "calls.jsonl")
        summary = run_pipeline(items, client, run_dir, args.out, args.budget, args.ablate)
        atomic_json(run_dir / "summary.json", {"status": "completed", **summary})
        print(f"Answers: {args.out}\nEvidence: {run_dir}")
        return 0
    except (OSError, ValueError, ModelRequestError, SolverError) as exc:
        if run_dir is not None:
            atomic_json(run_dir / "summary.json", {"status": "failed", "error": str(exc),
                "note": "No final output was published by this run. Any older output is unchanged."})
        print(f"Run failed: {exc}", file=sys.stderr)
        if run_dir:
            print(f"Evidence: {run_dir}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
