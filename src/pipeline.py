"""Budgeted extraction, validation, Z3 solving, and final answer JSON."""

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from .extractor import (build_messages, decode_extraction, header_from_item,
                        note_line_numbers, unclassified_lines)
from .higher_budget import (audit_messages, batch_messages, batches_for_item,
                            decode_audit, decode_batch)
from .model_client import BUDGETS, GraniteClient, MODEL, ModelConfig, ModelRequestError
from .output_writer import atomic_json, validate_answer, write_answers
from .schema import SchemaError, parse_problem
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


def answer_from_problem(problem, minimal_conflict_search: bool = True) -> tuple[dict, str, str | None]:
    try:
        solver = ScheduleSolver(problem)
        if not minimal_conflict_search and not solver.is_satisfiable():
            return {"case": "inconsistent", "conflicts": []}, "core_search_ablated", None
        try:
            answer = solver.result()
        except ExtractionIncompleteError as exc:
            # No second call exists at 1x. Return a documented partial attempt
            # rather than inventing rules. All emitted schedules satisfy extraction.
            answer = {"case": "ambiguous", "assignments": solver.all_solutions()[:4]}
            validate_answer(answer)
            return answer, "partial_extraction", str(exc)
        validate_answer(answer)
        return answer, "solved_from_extraction", None
    except (SchemaError, SolverError) as exc:
        # There is no abstention shape in the assignment. An empty conflicting
        # set earns zero credit; logs explicitly label this as unresolved, not
        # as a certified contradiction. Never fabricate names or citations.
        return {"case": "inconsistent", "conflicts": []}, "unresolved", str(exc)


def answer_from_response(response: str, item: dict, source_validation: bool = True,
                         minimal_conflict_search: bool = True) -> tuple[dict, str, str | None]:
    try:
        return answer_from_problem(decode_extraction(response, item, source_validation),
                                   minimal_conflict_search)
    except SchemaError as exc:
        return {"case": "inconsistent", "conflicts": []}, "unresolved", str(exc)


def solve_count(problem) -> int:
    return len(ScheduleSolver(problem).all_solutions())


def solve_item(item: dict, client: GraniteClient, record: dict, records: list,
               run_dir: Path, budget: str, ablate: str | None = None) -> tuple[dict, str, str | None]:
    """Make 1..budget model calls; every accepted edit is fully revalidated."""
    item_id = item["id"]
    cap = BUDGETS[budget]
    issues = []
    source_validation = ablate != "source_validation"
    prompt_examples = ablate != "prompt_examples"
    minimal_conflict_search = ablate != "minimal_conflict_search"

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
        """Use a higher-budget slot without discarding an earlier extraction on failure."""
        try:
            return call(kind, messages)
        except ModelRequestError as exc:
            issues.append(str(exc))
            return None

    messages = build_messages(item, prompt_examples)
    record["messages"] = messages
    response = call("full_extraction", messages)
    record["response"] = response
    problem = None
    try:
        problem = decode_extraction(response, item, source_validation,
                                    salvage=True, diagnostics=issues)
    except SchemaError as exc:
        issues.append(f"Initial extraction invalid: {exc}")

    if budget == "1x":
        answer, status, error = (answer_from_problem(problem, minimal_conflict_search)
                                 if problem else answer_from_response(
                                     response, item, source_validation, minimal_conflict_search))
        return answer, status, "; ".join(filter(None, [*issues, error])) or None

    if problem is None and ablate != "sentence_decomposition":
        # Recover from an invalid page response using independently validated
        # line batches. Failed batches are split, so their remaining lines can
        # still be recovered within the same per-item call cap.
        header = header_from_item(item)
        problem = parse_problem({**header, "constraints": [], "ignored_lines": []}, item["text"])
        pending = batches_for_item(item, min(4, cap - len(record["attempts"])))
        singleton_failures = {}
        while pending and len(record["attempts"]) < cap:
            target = [line for line in pending.pop(0) if line in unclassified_lines(problem, item)]
            if not target:
                continue
            raw = optional_call("bootstrap_batch", batch_messages(item, problem, target))
            if raw is not None:
                try:
                    problem = decode_batch(raw, item, problem, target, source_validation,
                                           salvage=True, diagnostics=issues)
                    continue
                except SchemaError as exc:
                    issues.append(f"Bootstrap batch {target} rejected: {exc}")
            if len(target) > 1:
                midpoint = len(target) // 2
                pending.extend((target[:midpoint], target[midpoint:]))
            else:
                line = target[0]
                singleton_failures[line] = singleton_failures.get(line, 0) + 1
                if singleton_failures[line] < 2:
                    pending.append(target)
                else:
                    issues.append(f"Bootstrap line {line} remains unclassified after two attempts")
        missing = unclassified_lines(problem, item)
        if missing:
            return ({"case": "inconsistent", "conflicts": []}, "unresolved",
                    "; ".join([*issues, f"Unclassified note lines: {missing}"]))
        if budget == "10x" and ablate != "extraction_review" and len(record["attempts"]) < cap:
            raw = optional_call("bootstrap_audit", audit_messages(item, problem))
            if raw is not None:
                try:
                    problem, _ = decode_audit(raw, item, problem, source_validation,
                                              salvage=True, diagnostics=issues)
                except SchemaError as exc:
                    issues.append(f"Bootstrap audit rejected: {exc}")
        if budget == "10x" and len(record["attempts"]) < cap and not unclassified_lines(problem, item):
            for target in batches_for_item(item, cap - len(record["attempts"])):
                raw = optional_call("small_batch_reextract", batch_messages(item, problem, target))
                if raw is not None:
                    try:
                        problem = decode_batch(raw, item, problem, target, source_validation,
                                               salvage=True, diagnostics=issues)
                    except SchemaError as exc:
                        issues.append(f"Batch {target} rejected: {exc}")
        missing = unclassified_lines(problem, item)
        if missing:
            return ({"case": "inconsistent", "conflicts": []}, "unresolved",
                    "; ".join([*issues, f"Unclassified note lines: {missing}"]))
        answer, status, error = answer_from_problem(problem, minimal_conflict_search)
        return answer, status, "; ".join(filter(None, [*issues, error])) or None

    while problem is None and len(record["attempts"]) < cap:
        replacement = optional_call("full_reextract", build_messages(item, prompt_examples))
        if replacement is None:
            continue
        try:
            problem = decode_extraction(replacement, item, source_validation,
                                        salvage=True, diagnostics=issues)
        except SchemaError as exc:
            issues.append(f"Full re-extraction invalid: {exc}")
    if problem is None:
        return {"case": "inconsistent", "conflicts": []}, "unresolved", "; ".join(issues)

    # Audit every valid first pass, even if it yields 1..4 solutions: the
    # solution count alone cannot expose a plausible but incorrect rule.
    changed_lines = []
    if ablate != "extraction_review" and len(record["attempts"]) < cap:
        raw = optional_call("source_audit", audit_messages(item, problem))
        if raw is not None:
            try:
                problem, changed_lines = decode_audit(raw, item, problem, source_validation,
                                                      salvage=True, diagnostics=issues)
            except SchemaError as exc:
                issues.append(f"Audit rejected: {exc}")

    if budget == "3x" and ablate != "sentence_decomposition" and len(record["attempts"]) < cap:
        target = changed_lines
        if not target and solve_count(problem) not in {1, 2, 3, 4}:
            # Anomalous count: re-extract the non-header lines as a focused
            # one-call batch. Genuine contradictions are allowed to remain.
            target = note_line_numbers(item)
        if target:
            raw = optional_call("focused_reextract", batch_messages(item, problem, target))
            if raw is not None:
                try:
                    problem = decode_batch(raw, item, problem, target, source_validation,
                                           salvage=True, diagnostics=issues)
                except SchemaError as exc:
                    issues.append(f"Focused batch rejected: {exc}")

    if budget == "10x" and ablate != "sentence_decomposition":
        remaining = cap - len(record["attempts"])
        for target in batches_for_item(item, remaining):
            raw = optional_call("small_batch_reextract", batch_messages(item, problem, target))
            if raw is not None:
                try:
                    problem = decode_batch(raw, item, problem, target, source_validation,
                                           salvage=True, diagnostics=issues)
                except SchemaError as exc:
                    issues.append(f"Batch {target} rejected: {exc}")

    missing = unclassified_lines(problem, item)
    if missing:
        return ({"case": "inconsistent", "conflicts": []}, "unresolved",
                "; ".join([*issues, f"Unclassified note lines: {missing}"]))
    answer, status, error = answer_from_problem(problem, minimal_conflict_search)
    combined = "; ".join(filter(None, [*issues, error])) or None
    return answer, status, combined


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
