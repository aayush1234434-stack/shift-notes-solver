"""Single-call extraction messages and deterministic response validation."""

import json
import re
from pathlib import Path

from .schema import SchemaError, parse_problem

ROOT = Path(__file__).resolve().parents[1]
ORDER_TYPES = {"before", "after", "immediately_before", "immediately_after", "between",
               "station_before_person", "station_after_person", "not_between"}
ORDER_CONNECTOR = re.compile(
    r"\b(immediately before|directly before|earlier in the day than|earlier than|"
    r"precedes|before|immediately after|directly after|later in the day than|"
    r"later than|follows|after)\b", re.IGNORECASE)
NONCURRENT = re.compile(
    r"\b(last month|previous cycle|old arrangement|back in the old|"
    r"in the spring|would have|had the rota gone|lobbied for|wanted|"
    r"put in for|asked to move|request was declined|without success)\b",
    re.IGNORECASE)
VISIBLE_HEADER = re.compile(
    r"(?:[^.\n]*\.\s*)?(?P<count>\d+) staff on the rota: (?P<people>.*?)\. Blocks run "
    r"(?P<blocks>.*?), one person per block, and each person works exactly one block\. "
    r"There are (?P<station_count>\d+) stations, one person on each: "
    r"(?P<stations>.*?)\. The people on a station are (?P<station_holders>.*?); "
    r"the rest are on no station\."
)
SIMPLE_HEADER = re.compile(
    r"Staff: (?P<people>.*?)\. Blocks: (?P<blocks>.*?), one person each\. "
    r"Station holders: (?P<station_holders>.*?)\. Stations: "
    r"(?P<stations>.*?), one person each\."
)


def numbered_lines(item: dict) -> list[str]:
    return [line for line in item["text"].splitlines() if line.strip()]


def _header_match(item: dict):
    matches = [(number, match) for number, line in enumerate(numbered_lines(item), 1)
               if (match := VISIBLE_HEADER.fullmatch(line) or SIMPLE_HEADER.fullmatch(line))]
    if len(matches) != 1:
        raise SchemaError(f"Expected exactly one complete rota header line; found {len(matches)}")
    return matches[0]


def header_line_number(item: dict) -> int:
    return _header_match(item)[0]


def note_line_numbers(item: dict) -> list[int]:
    header_number = header_line_number(item)
    return [number for number in range(1, len(numbered_lines(item)) + 1)
            if number != header_number]


def header_from_item(item: dict) -> dict[str, list[str]]:
    """Read the standardized rota header without asking Granite to reproduce it."""
    _, match = _header_match(item)
    values = {name: match[name].split(", ")
              for name in ("people", "blocks", "stations", "station_holders")}
    if match.re is VISIBLE_HEADER and (
        len(values["people"]) != int(match["count"])
        or len(values["stations"]) != int(match["station_count"])
    ):
        raise SchemaError("Header counts do not match its lists")
    for field, expected in (("n_staff", len(values["people"])),
                            ("n_stations", len(values["stations"]))):
        if field in item and item[field] != expected:
            raise SchemaError(f"Header disagrees with {field}")
    parse_problem({**values, "constraints": []})
    return values


def _entities_in(text: str, values: list[str]) -> list[str]:
    return [value for value in values if re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", text)]


def _unambiguous_order(line: str, header: dict) -> dict | None:
    """Verify explicit two-operand ordering; leave complex prose to the model."""
    people = header["people"]
    stations = header["stations"]
    between_match = re.search(r"\b(after|before) one of\b", line, re.IGNORECASE)
    if between_match and re.search(r"\band (?:before|after) the other\b", line, re.IGNORECASE):
        subject = _entities_in(line[:between_match.start()], people)
        others = _entities_in(line[between_match.end():], people)
        if len(subject) == 1 and len(others) == 2:
            return {"type": "between", "person": subject[0],
                    "other_person": others[0], "third_person": others[1]}
    if "no block between" in line.casefold() and "in that order" in line.casefold():
        named = [(line.find(name), name) for name in people if name in line]
        if len(named) == 2:
            first, second = [name for _, name in sorted(named)]
            return {"type": "immediately_before", "person": first, "other_person": second}
    match = ORDER_CONNECTOR.search(line)
    if match is None:
        return None
    # "Not before" and similar negations are not positive precedence claims.
    if re.search(r"\bnot\s+$", line[max(0, match.start() - 12):match.start()], re.IGNORECASE):
        return None
    left, right = line[:match.start()], line[match.end():]
    left_people, right_people = _entities_in(left, people), _entities_in(right, people)
    left_stations, right_stations = _entities_in(left, stations), _entities_in(right, stations)
    if len(left_people) + len(right_people) != 2 and not (
        len(left_people) + len(right_people) == 1
        and len(left_stations) + len(right_stations) == 1
    ):
        return None
    earlier = match.group(1).casefold() in {"before", "precedes", "earlier than",
                                             "earlier in the day than", "immediately before",
                                             "directly before"}
    immediate = "immediately" in match.group(1).casefold() or "directly" in match.group(1).casefold()
    if len(left_people) == len(right_people) == 1 and not left_stations and not right_stations:
        first = left_people[0] if earlier else right_people[0]
        second = right_people[0] if earlier else left_people[0]
        return {"type": "immediately_before" if immediate else "before",
                "person": first, "other_person": second}
    if len(left_stations) == len(right_people) == 1 and not left_people and not right_stations:
        return {"type": "station_before_person" if earlier else "station_after_person",
                "station": left_stations[0], "person": right_people[0]}
    if len(left_people) == len(right_stations) == 1 and not left_stations and not right_people:
        return {"type": "station_after_person" if earlier else "station_before_person",
                "station": right_stations[0], "person": left_people[0]}
    return None


def build_messages(item: dict, prompt_examples: bool = True) -> list[dict[str, str]]:
    lines = numbered_lines(item)
    header = header_from_item(item)
    prompt = (ROOT / "prompts" / "extract.txt").read_text(encoding="utf-8")
    if not prompt_examples:
        start = prompt.index("Generic examples of language meanings")
        end = prompt.index("Extract every definite constraint", start)
        prompt = prompt[:start] + prompt[end:]
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": "Header lists (already parsed; do not extract the header):\n" +
         json.dumps(header, ensure_ascii=False) + "\n\nNumbered note lines:\n" +
         "\n".join(f"{number}: {lines[number - 1]}" for number in note_line_numbers(item))},
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


def decode_extraction(response: str, item: dict, source_validation: bool = True,
                      salvage: bool = False, diagnostics: list[str] | None = None,
                      require_complete: bool = True):
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
    if not isinstance(data, dict):
        raise SchemaError("Extraction must be a JSON object")
    header = header_from_item(item)
    data = dict(data)
    for name, values in header.items():
        if name in data and data[name] != values:
            raise SchemaError(f"Extracted {name} disagrees with the header")
        data[name] = values
    if not isinstance(data.get("constraints"), list):
        raise SchemaError("constraints must be a list")
    if not isinstance(data.get("ignored_lines"), list):
        raise SchemaError("ignored_lines must be an explicit list")
    lines = numbered_lines(item)
    note_lines = set(note_line_numbers(item))
    ignored = data["ignored_lines"]
    if (any(type(number) is not int or number not in note_lines for number in ignored)
            or len(ignored) != len(set(ignored))):
        raise SchemaError("ignored_lines must contain unique non-header line numbers")
    cited = set()
    for index, rule in enumerate(data["constraints"]):
        if not isinstance(rule, dict) or type(rule.get("source_line")) is not int or rule["source_line"] not in note_lines:
            raise SchemaError(f"Constraint {index} has an invalid non-header source_line")
        cited.add(rule["source_line"])
    if cited & set(ignored):
        raise SchemaError(f"Lines both constrained and ignored: {sorted(cited & set(ignored))}")
    if require_complete and note_lines - cited - set(ignored):
        raise SchemaError(f"Unclassified note lines: {sorted(note_lines - cited - set(ignored))}")
    if source_validation:
        constraints = []
        seen = set()
        for index, raw_rule in enumerate(data["constraints"]):
            try:
                if not isinstance(raw_rule, dict):
                    raise SchemaError(f"Constraint {index} must be an object")
                number, evidence = raw_rule.get("source_line"), raw_rule.get("source")
                if type(number) is not int or number not in note_lines:
                    raise SchemaError(f"Constraint {index} has an invalid non-header source_line")
                line = lines[number - 1]
                if not isinstance(evidence, str) or not evidence.strip() or evidence not in line:
                    raise SchemaError(f"Constraint {index} has evidence absent from line {number}")
                if salvage and NONCURRENT.search(line):
                    raise SchemaError(f"Constraint {index} cites a non-current or hypothetical line")
                rule = {**raw_rule, "source": line}
                if salvage and rule.get("type") in ORDER_TYPES:
                    inferred = _unambiguous_order(line, header)
                    if inferred is not None:
                        rule = {"source_line": number, "source": line, **inferred}
                if salvage:
                    for field in ("person", "other_person", "third_person", "station", "block"):
                        if (field in rule and isinstance(rule[field], str)
                                and rule[field].casefold() not in line.casefold()):
                            raise SchemaError(
                                f"Constraint {index} has {field} absent from its source line")
                parse_problem({**header, "constraints": [rule]}, raw_text=item["text"])
                signature = json.dumps(rule, sort_keys=True)
                if signature not in seen:
                    constraints.append(rule)
                    seen.add(signature)
            except SchemaError as exc:
                if not salvage:
                    raise
                if diagnostics is not None:
                    diagnostics.append(f"Dropped model rule {index}: {exc}")
        data["constraints"] = constraints
    surviving = {rule["source_line"] for rule in data["constraints"]}
    if require_complete and note_lines - surviving - set(ignored):
        raise SchemaError(f"No valid classification remains for lines: {sorted(note_lines - surviving - set(ignored))}")
    problem = parse_problem(data, raw_text=item["text"] if source_validation else None)
    for name, values in (("people", problem.people), ("blocks", problem.blocks),
                         ("stations", problem.stations), ("station_holders", problem.station_holders)):
        if any(value not in item["text"] for value in values):
            raise SchemaError(f"{name} contains strings absent from the original notes")
    for field, actual in (("n_staff", len(problem.people)), ("n_stations", len(problem.stations))):
        if field in item and item[field] != actual:
            raise SchemaError(f"Extracted header disagrees with {field}")
    return problem


def unclassified_lines(problem, item: dict) -> list[int]:
    """Return note lines with neither an accepted rule nor an explicit ignore."""
    classified = {rule.source_line for rule in problem.constraints} | set(problem.ignored_lines)
    return sorted(set(note_line_numbers(item)) - classified)
