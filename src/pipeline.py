"""Phase 4: one extraction call per item, validation, Z3, and final JSON."""

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from .extractor import build_messages, decode_extraction
from .model_client import GraniteClient, MODEL, ModelConfig, ModelRequestError
from .output_writer import atomic_json, validate_answer, write_answers
from .schema import SchemaError
from .solver import ExtractionIncompleteError, ScheduleSolver, SolverError

ROOT = Path(__file__).resolve().parents[1]


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


def answer_from_response(response: str, item: dict) -> tuple[dict, str, str | None]:
    try:
        problem = decode_extraction(response, item)
        solver = ScheduleSolver(problem)
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


def run_pipeline(items: list[dict], client: GraniteClient, run_dir: Path, output: Path) -> dict:
    validate_items(items)
    answers = {}
    records = []
    for position, item in enumerate(items, 1):
        messages = build_messages(item)
        record = {"item_id": item["id"], "messages": messages, "response": None,
                  "status": "request_pending", "error": None, "answer": None}
        records.append(record)
        atomic_json(run_dir / "records.json", records)
        try:
            record["response"] = client.complete(item["id"], messages)
        except ModelRequestError as exc:
            record.update(status="endpoint_failure", error=str(exc))
            atomic_json(run_dir / "records.json", records)
            atomic_json(run_dir / "call_counts.json", client.call_counts)
            raise
        answer, status, error = answer_from_response(record["response"], item)
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
    parser.add_argument("--budget", required=True, choices=["1x"], help="Phase 4 implements 1x only")
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
        prompt = (ROOT / "prompts" / "extract.txt").read_bytes()
        atomic_json(run_dir / "manifest.json", {
            "created": datetime.now(timezone.utc).isoformat(), "budget": args.budget,
            "model": MODEL, "temperature": 1.0, "top_p": 0.95,
            "reasoning": {"enabled": False}, "items": len(items),
            "input_sha256": hashlib.sha256(args.items.read_bytes()).hexdigest(),
            "prompt_sha256": hashlib.sha256(prompt).hexdigest(),
            "output": str(args.out.resolve()), "item_ids": [item["id"] for item in items]})
        client = GraniteClient(config, args.budget, run_dir / "calls.jsonl")
        summary = run_pipeline(items, client, run_dir, args.out)
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
