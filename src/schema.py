from dataclasses import dataclass

RELATION_FIELDS = {
    "fixed_block": ("person", "block"), "not_block": ("person", "block"),
    "fixed_station": ("person", "station"), "not_station": ("person", "station"),
    **{kind: ("person", "other_person") for kind in (
        "before", "after", "immediately_before", "immediately_after",
        "adjacent", "not_adjacent")},
    "between": ("person", "other_person", "third_person"),
    "station_before_person": ("station", "person"),
    "station_after_person": ("station", "person"),
}


class SchemaError(ValueError):
    pass


@dataclass(frozen=True)
class Constraint:
    type: str
    person: str
    source_line: int
    source: str
    other_person: str | None = None
    third_person: str | None = None
    block: str | None = None
    station: str | None = None


@dataclass(frozen=True)
class Problem:
    people: tuple[str, ...]
    blocks: tuple[str, ...]
    stations: tuple[str, ...]
    station_holders: tuple[str, ...]
    constraints: tuple[Constraint, ...]
    ignored_lines: tuple[int, ...] = ()


def parse_problem(data: dict, raw_text: str | None = None) -> Problem:
    # Lines are the nonempty lines of the item, counted from 1. Blocks are in time order.
    # Strings are kept exactly: the scorer compares names and citations byte for byte.
    required = {"people", "blocks", "stations", "station_holders", "constraints"}
    if not isinstance(data, dict) or not required <= data.keys():
        raise SchemaError("Missing required extraction fields")
    if data.keys() - required - {"ignored_lines"}:
        raise SchemaError("Unexpected extraction fields")
    groups = {}
    for name in ("people", "blocks", "stations", "station_holders"):
        values = data[name]
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value
            for value in values
        ):
            raise SchemaError(f"{name} must be a list of nonempty single-line strings")
        if len(set(values)) != len(values):
            raise SchemaError(f"Duplicate values in {name}")
        groups[name] = tuple(values)
    if not groups["people"] or len(groups["people"]) != len(groups["blocks"]):
        raise SchemaError("Require one block per person and at least one person")
    if len(groups["stations"]) != len(groups["station_holders"]):
        raise SchemaError("Require one station per station holder")
    if not set(groups["station_holders"]) <= set(groups["people"]):
        raise SchemaError("Station holders must be on the rota")
    if not isinstance(data["constraints"], list):
        raise SchemaError("constraints must be a list")
    lines = None if raw_text is None else [line for line in raw_text.splitlines() if line.strip()]
    constraints = []
    sources = {}
    for index, value in enumerate(data["constraints"]):
        if not isinstance(value, dict):
            raise SchemaError(f"Constraint {index} must be an object")
        kind = value.get("type")
        if not isinstance(kind, str) or kind not in RELATION_FIELDS:
            raise SchemaError(f"Unsupported constraint type at index {index}")
        fields = {"type", "source", "source_line", *RELATION_FIELDS[kind]}
        if set(value) != fields:
            raise SchemaError(f"Constraint {index} must have exactly {sorted(fields)}")
        for field in RELATION_FIELDS[kind]:
            group = "blocks" if field == "block" else "stations" if field == "station" else "people"
            if not isinstance(value[field], str) or value[field] not in groups[group]:
                raise SchemaError(f"Constraint {index} refers to an unknown {field}")
        if kind in {"fixed_station", "not_station"} and value["person"] not in groups["station_holders"]:
            raise SchemaError("Station constraints must refer to a station holder")
        number, source = value["source_line"], value["source"]
        if type(number) is not int or number < 1:
            raise SchemaError("source_line must be a positive integer")
        if not isinstance(source, str) or not source.strip() or "\n" in source or "\r" in source:
            raise SchemaError("source must contain one complete original line")
        if number in sources and sources[number] != source:
            raise SchemaError("One source line cannot have different source strings")
        if lines is not None and (number > len(lines) or lines[number - 1] != source):
            raise SchemaError(f"Source does not match raw text at line {number}")
        sources[number] = source
        constraints.append(Constraint(**value))
    ignored = data.get("ignored_lines", [])
    if not isinstance(ignored, list) or any(type(number) is not int or number < 1 for number in ignored):
        raise SchemaError("ignored_lines must contain positive integers")
    if len(set(ignored)) != len(ignored) or set(ignored) & sources.keys():
        raise SchemaError("Duplicate or conflicting ignored line classification")
    if lines is not None and any(number > len(lines) for number in ignored):
        raise SchemaError("Ignored line is outside raw text")
    return Problem(**groups, constraints=tuple(constraints), ignored_lines=tuple(ignored))
