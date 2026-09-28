"""Shape validation and atomic publication of final answer files."""

import json
import os
import tempfile
from pathlib import Path


def validate_answer(answer: dict) -> None:
    if not isinstance(answer, dict):
        raise ValueError("Answer must be an object")
    case = answer.get("case")
    fields = {"unique": "assignment", "ambiguous": "assignments", "inconsistent": "conflicts"}
    if case not in fields or set(answer) != {"case", fields[case]}:
        raise ValueError("Invalid answer case or fields")
    if case == "inconsistent":
        if not isinstance(answer["conflicts"], list) or any(
            not isinstance(source, str) or not source or "\n" in source or "\r" in source
            for source in answer["conflicts"]
        ):
            raise ValueError("Invalid conflict citations")
        return
    assignments = [answer["assignment"]] if case == "unique" else answer["assignments"]
    if not isinstance(assignments, list) or not 1 <= len(assignments) <= 4:
        raise ValueError("Invalid assignment count")
    if case == "ambiguous" and len(assignments) < 2:
        raise ValueError("Ambiguous output needs multiple schedules")
    for assignment in assignments:
        if not isinstance(assignment, dict) or not assignment:
            raise ValueError("Assignment must include people")
        for person, values in assignment.items():
            if not isinstance(person, str) or not isinstance(values, dict) or "block" not in values:
                raise ValueError("Invalid assignment fields")
            if set(values) - {"block", "station"} or any(
                not isinstance(value, str) or not value for value in values.values()
            ):
                raise ValueError("Invalid block or station")
    if len({json.dumps(a, sort_keys=True) for a in assignments}) != len(assignments):
        raise ValueError("Duplicate schedules")


def atomic_json(path: Path, value) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".tmp-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def write_answers(path: Path, answers: dict, expected_ids: list[str]) -> None:
    if set(answers) != set(expected_ids):
        raise ValueError("Output must contain every input ID and no extra IDs")
    for answer in answers.values():
        validate_answer(answer)
    atomic_json(path, answers)
