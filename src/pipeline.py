"""./run entrypoint: menu selection with Granite, then Z3."""

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
from .output_writer import write_answers, write_json
from .schema import SchemaError
from .solver import ExtractionIncompleteError, ScheduleSolver, SolverError

ROOT = Path(__file__).resolve().parents[1]
ABLATIONS = ("prompt_examples", "source_validation", "extraction_review",
             "sentence_decomposition", "minimal_conflict_search")


def validate_items(items) -> None:
    if not isinstance(items, list) or not items:
        raise ValueError("Input must be a nonempty JSON array")
    ids = [item.get("id") for item in items]
    if not all(isinstance(item_id, str) and item_id for item_id in ids):
        raise ValueError("Every item needs an id")
    # The budget is counted per id at the proxy, so two items cannot share one.
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
            # More than four schedules is never a valid answer. Return four real ones
            # rather than inventing a rule to cut the count down.
            answer = {"case": "ambiguous", "assignments": solver.all_solutions()[:4]}
            validate_answer_for_problem(answer, problem, allow_partial=True)
            return answer, "partial_extraction", str(exc)
        validate_answer_for_problem(answer, problem)
        return answer, "solved_from_extraction", None
    except (SchemaError, SolverError, AnswerValidationError) as exc:
        # The format has no "don't know". An empty conflict list scores zero,
        # which beats citing lines we could not show conflict.
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
    for number, key in parse_choices(response).items():
        if allowed is not None and number not in allowed:
            continue
        choices[number] = key


def solve_item(item: dict, client: GraniteClient, record: dict, records: list,
               run_dir: Path, budget: str, ablate: str | None = None) -> tuple[dict, str, str | None]:
    # Only a letter Granite picked becomes a rule. Nothing falls back to the parser.
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
        try:
            response = client.complete(item_id, messages)
        except ModelRequestError as exc:
            attempt["error"] = str(exc)
            record.update(status="endpoint_failure", error=str(exc))
            raise
        attempt["response"] = response
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
        try:
            answer, status, error = solve_item(item, client, record, records, run_dir, budget, ablate)
            answers[item["id"]] = answer
            record.update(status=status, error=error, answer=answer)
        finally:
            write_json(run_dir / "records.json", records)
            write_json(run_dir / "call_counts.json", client.call_counts)
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
        prompt_hash = hashlib.sha256((ROOT / "prompts" / "extract.txt").read_bytes()).hexdigest()
        write_json(run_dir / "manifest.json", {
            "created": datetime.now(timezone.utc).isoformat(), "budget": args.budget,
            "ablate": args.ablate,
            "model": MODEL, "temperature": 1.0, "top_p": 0.95,
            "reasoning": {"enabled": False}, "items": len(items),
            "input_sha256": hashlib.sha256(args.items.read_bytes()).hexdigest(),
            "prompt_sha256": prompt_hash,
            "output": str(args.out.resolve()), "item_ids": [item["id"] for item in items]})
        client = GraniteClient(config, args.budget, run_dir / "calls.jsonl")
        summary = run_pipeline(items, client, run_dir, args.out, args.budget, args.ablate)
        write_json(run_dir / "summary.json", {"status": "completed", **summary})
        print(f"Answers: {args.out}\nEvidence: {run_dir}")
        return 0
    except (OSError, ValueError, ModelRequestError, SolverError) as exc:
        if run_dir is not None:
            write_json(run_dir / "summary.json", {"status": "failed", "error": str(exc)})
        print(f"Run failed: {exc}", file=sys.stderr)
        if run_dir:
            print(f"Evidence: {run_dir}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
