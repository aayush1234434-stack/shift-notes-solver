"""Focused audit and batch extraction for 3x/10x within per-item call caps."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .extractor import _reject_constant, _unique_object, decode_extraction, unclassified_lines
from .schema import Problem, SchemaError

ROOT = Path(__file__).resolve().parents[1]


def numbered_lines(item: dict) -> list[str]:
    return [line for line in item["text"].splitlines() if line.strip()]


def _parse_json(raw: str):
    text = raw.strip()
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[len("```json\n"):-len("\n```")]
    elif text.startswith("```\n") and text.endswith("\n```"):
        text = text[len("```\n"):-len("\n```")]
    try:
        return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise SchemaError(f"Malformed review JSON at character {exc.pos}") from None


def problem_data(problem: Problem) -> dict:
    return {
        "people": list(problem.people), "blocks": list(problem.blocks),
        "stations": list(problem.stations), "station_holders": list(problem.station_holders),
        "constraints": [
            {field: value for field, value in asdict(rule).items() if value is not None}
            for rule in problem.constraints
        ],
        "ignored_lines": list(problem.ignored_lines),
    }


def audit_messages(item: dict, problem: Problem) -> list[dict[str, str]]:
    lines = numbered_lines(item)
    prompt = (ROOT / "prompts" / "audit.txt").read_text(encoding="utf-8")
    original = "\n".join(f"{number}: {line}" for number, line in enumerate(lines, 1))
    return [{"role": "system", "content": prompt},
            {"role": "user", "content": "Original notes:\n" + original +
             "\n\nCurrent extraction:\n" + json.dumps(problem_data(problem), ensure_ascii=False)}]


def batch_messages(item: dict, problem: Problem, line_numbers: list[int]) -> list[dict[str, str]]:
    lines = numbered_lines(item)
    if not line_numbers or any(type(n) is not int or not 2 <= n <= len(lines) for n in line_numbers):
        raise ValueError("Batch lines must be non-header source lines")
    prompt = (ROOT / "prompts" / "batch_extract.txt").read_text(encoding="utf-8")
    header = {key: value for key, value in problem_data(problem).items()
              if key in {"people", "blocks", "stations", "station_holders"}}
    selected = "\n".join(f"{number}: {lines[number - 1]}" for number in line_numbers)
    return [{"role": "system", "content": prompt},
            {"role": "user", "content": "Header lists:\n" +
             json.dumps(header, ensure_ascii=False) + "\n\nLines to extract:\n" + selected}]


def _validate_edits(item: dict, edits, allowed_lines: set[int],
                    source_validation: bool = True) -> list[dict]:
    if not isinstance(edits, list):
        raise SchemaError("edits must be a list")
    original = numbered_lines(item)
    seen = set()
    validated = []
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {"source_line", "constraints"}:
            raise SchemaError("Each edit needs source_line and constraints only")
        number = edit["source_line"]
        if type(number) is not int or number not in allowed_lines or number in seen:
            raise SchemaError("Edit has a duplicate or out-of-scope source line")
        if not isinstance(edit["constraints"], list):
            raise SchemaError("Edit constraints must be a list")
        rules = []
        for rule in edit["constraints"]:
            if not isinstance(rule, dict) or rule.get("source_line") != number or (
                source_validation and (
                    not isinstance(rule.get("source"), str)
                    or rule["source"] not in original[number - 1]
                )
            ):
                raise SchemaError("Edited constraint must cite its numbered source")
            rules.append({**rule, "source": original[number - 1]} if source_validation else rule)
        seen.add(number)
        validated.append({"source_line": number, "constraints": rules})
    return validated


def apply_edits(item: dict, problem: Problem, edits: list[dict],
                source_validation: bool = True, salvage: bool = False,
                diagnostics: list[str] | None = None) -> Problem:
    allowed = set(range(2, len(numbered_lines(item)) + 1))
    edits = _validate_edits(item, edits, allowed, source_validation)
    data = problem_data(problem)
    replaced = {edit["source_line"] for edit in edits}
    data["constraints"] = [rule for rule in data["constraints"] if rule["source_line"] not in replaced]
    ignored = set(data["ignored_lines"]) - replaced
    for edit in edits:
        data["constraints"].extend(edit["constraints"])
        if not edit["constraints"]:
            ignored.add(edit["source_line"])
    data["ignored_lines"] = sorted(ignored)
    updated = decode_extraction(json.dumps(data, ensure_ascii=False), item,
                                source_validation=source_validation,
                                salvage=salvage, diagnostics=diagnostics,
                                require_complete=False)
    expected_rules = {edit["source_line"] for edit in edits if edit["constraints"]}
    surviving_rules = {rule.source_line for rule in updated.constraints}
    if expected_rules - surviving_rules:
        raise SchemaError(f"No valid rule remains for edited lines: {sorted(expected_rules - surviving_rules)}")
    return updated


def decode_audit(raw: str, item: dict, problem: Problem,
                 source_validation: bool = True, salvage: bool = False,
                 diagnostics: list[str] | None = None) -> tuple[Problem, list[int]]:
    data = _parse_json(raw)
    if not isinstance(data, dict) or set(data) != {"edits"}:
        raise SchemaError("Audit response needs only an edits array")
    edits = _validate_edits(item, data["edits"],
                            set(range(2, len(numbered_lines(item)) + 1)), source_validation)
    return (apply_edits(item, problem, edits, source_validation, salvage, diagnostics),
            [edit["source_line"] for edit in edits])


def decode_batch(raw: str, item: dict, problem: Problem, line_numbers: list[int],
                 source_validation: bool = True, salvage: bool = False,
                 diagnostics: list[str] | None = None) -> Problem:
    data = _parse_json(raw)
    if not isinstance(data, dict) or set(data) != {"constraints", "ignored_lines"}:
        raise SchemaError("Batch response needs constraints and ignored_lines")
    allowed = set(line_numbers)
    rules = data["constraints"]
    ignored = data["ignored_lines"]
    if not isinstance(rules, list) or not isinstance(ignored, list) or any(
        type(number) is not int or number not in allowed for number in ignored
    ) or len(ignored) != len(set(ignored)):
        raise SchemaError("Batch classifications must use unique requested lines")
    grouped = {number: [] for number in line_numbers}
    cited = set()
    raw_cited = set()
    original = numbered_lines(item)
    for rule in rules:
        if isinstance(rule, dict) and type(rule.get("source_line")) is int:
            raw_cited.add(rule["source_line"])
        if not isinstance(rule, dict) or type(rule.get("source_line")) is not int:
            if salvage:
                if diagnostics is not None:
                    diagnostics.append("Dropped batch rule with invalid source_line")
                continue
            raise SchemaError("Invalid batch constraint")
        number = rule["source_line"]
        if number not in allowed or (source_validation and (
            not isinstance(rule.get("source"), str)
            or rule["source"] not in original[number - 1]
        )):
            if salvage:
                if diagnostics is not None:
                    diagnostics.append(f"Dropped batch rule without requested-line evidence: {number}")
                continue
            raise SchemaError("Batch constraint must quote a requested line")
        grouped[number].append({**rule, "source": original[number - 1]}
                               if source_validation else rule)
        cited.add(number)
    if raw_cited & set(ignored):
        raise SchemaError(f"Batch lines both constrained and ignored: {sorted(raw_cited & set(ignored))}")
    if allowed - cited - set(ignored):
        raise SchemaError(f"Unclassified batch lines: {sorted(allowed - cited - set(ignored))}")
    updated = apply_edits(item, problem, [
        {"source_line": number, "constraints": grouped[number]} for number in line_numbers
    ], source_validation, salvage, diagnostics)
    missing = allowed & set(unclassified_lines(updated, item))
    if missing:
        raise SchemaError(f"No valid batch classification remains for lines: {sorted(missing)}")
    return updated


def batches_for_item(item: dict, max_batches: int) -> list[list[int]]:
    line_numbers = list(range(2, len(numbered_lines(item)) + 1))
    count = min(max_batches, len(line_numbers))
    if not count:
        return []
    return [line_numbers[index::count] for index in range(count)]
