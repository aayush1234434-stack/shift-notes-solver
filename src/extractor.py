"""Single-call extraction messages and deterministic response validation."""

import json
from pathlib import Path

from .schema import SchemaError, parse_problem

ROOT = Path(__file__).resolve().parents[1]


def build_messages(item: dict) -> list[dict[str, str]]:
    lines = [line for line in item["text"].splitlines() if line.strip()]
    return [
        {"role": "system", "content": (ROOT / "prompts" / "extract.txt").read_text(encoding="utf-8")},
        {"role": "user", "content": "\n".join(
            f"{number}: {line}" for number, line in enumerate(lines, 1))},
    ]


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SchemaError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise SchemaError(f"Invalid JSON constant: {value}")


def decode_extraction(response: str, item: dict):
    text = response.strip()
    # Deterministic envelope removal only; never rewrite extracted facts.
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[len("```json\n"):-len("\n```")]
    elif text.startswith("```\n") and text.endswith("\n```"):
        text = text[len("```\n"):-len("\n```")]
    try:
        data = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise SchemaError(f"Malformed extraction JSON at character {exc.pos}") from None
    problem = parse_problem(data, raw_text=item["text"])
    for name, values in (("people", problem.people), ("blocks", problem.blocks),
                         ("stations", problem.stations), ("station_holders", problem.station_holders)):
        if any(value not in item["text"] for value in values):
            raise SchemaError(f"{name} contains strings absent from the original notes")
    for field, actual in (("n_staff", len(problem.people)), ("n_stations", len(problem.stations))):
        if field in item and item[field] != actual:
            raise SchemaError(f"Extracted header disagrees with {field}")
    return problem
