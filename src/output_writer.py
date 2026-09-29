import json
import os
from pathlib import Path


def validate_answer(answer: dict) -> None:
    if not isinstance(answer, dict):
        raise ValueError("Answer must be an object")
    case = answer.get("case")
    fields = {"unique": "assignment", "ambiguous": "assignments", "inconsistent": "conflicts"}
    if case not in fields or set(answer) != {"case", fields[case]}:
        raise ValueError("Invalid answer case or fields")
    if case == "inconsistent":
        if not isinstance(answer["conflicts"], list) or not all(
                isinstance(source, str) and source for source in answer["conflicts"]):
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
        for values in assignment.values():
            if not isinstance(values, dict) or "block" not in values or set(values) - {"block", "station"}:
                raise ValueError("Invalid assignment fields")
    if len({json.dumps(a, sort_keys=True) for a in assignments}) != len(assignments):
        raise ValueError("Duplicate schedules")


def write_json(path: Path, value) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_answers(path: Path, answers: dict, expected_ids: list[str]) -> None:
    if set(answers) != set(expected_ids):
        raise ValueError("Output must contain every input ID and no extra IDs")
    for answer in answers.values():
        validate_answer(answer)
    # Write beside the target and rename, so a crash never leaves half an answers file.
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    write_json(temporary, answers)
    os.replace(temporary, path)
