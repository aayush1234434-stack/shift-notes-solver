"""Independent, exhaustive checks of answers against extracted constraints.

This deliberately does not use Z3 or the constraint compiler. The assignment's
at-most-five-person search space is small enough to cross-check every schedule.
"""

import itertools
import json
import math

from .output_writer import validate_answer
from .schema import Problem


class AnswerValidationError(ValueError):
    pass


def _satisfies(rule, blocks: dict[str, int], stations: dict[str, str],
               problem: Problem) -> bool:
    kind = rule.type
    a = blocks[rule.person]
    if kind == "fixed_block":
        return a == problem.blocks.index(rule.block)
    if kind == "not_block":
        return a != problem.blocks.index(rule.block)
    if kind == "fixed_station":
        return stations[rule.person] == rule.station
    if kind == "not_station":
        return stations[rule.person] != rule.station
    if kind in {"station_before_person", "station_after_person"}:
        holder = next(person for person, station in stations.items() if station == rule.station)
        return blocks[holder] < a if kind == "station_before_person" else blocks[holder] > a
    b = blocks[rule.other_person]
    if kind == "before":
        return a < b
    if kind == "after":
        return a > b
    if kind == "immediately_before":
        return a + 1 == b
    if kind == "immediately_after":
        return a == b + 1
    if kind == "adjacent":
        return abs(a - b) == 1
    if kind == "not_adjacent":
        return abs(a - b) != 1
    if kind == "between":
        c = blocks[rule.third_person]
        return min(b, c) < a < max(b, c)
    raise AnswerValidationError(f"Unsupported rule type: {kind}")


def reference_schedules(problem: Problem, source_lines: set[int] | None = None) -> list[dict]:
    """Enumerate schedules without using the production solver."""
    count = math.factorial(len(problem.people)) * math.factorial(len(problem.stations))
    if count > 100_000:
        raise AnswerValidationError("Independent schedule check exceeds its size limit")
    rules = [rule for rule in problem.constraints
             if source_lines is None or rule.source_line in source_lines]
    schedules = []
    for block_order in itertools.permutations(range(len(problem.blocks))):
        blocks = dict(zip(problem.people, block_order))
        for station_order in itertools.permutations(problem.stations):
            stations = dict(zip(problem.station_holders, station_order))
            if not all(_satisfies(rule, blocks, stations, problem) for rule in rules):
                continue
            assignment = {person: {"block": problem.blocks[blocks[person]]}
                          for person in problem.people}
            for person, station in stations.items():
                assignment[person]["station"] = station
            schedules.append(assignment)
    return schedules


def _signature(assignment: dict) -> str:
    return json.dumps(assignment, ensure_ascii=False, sort_keys=True)


def validate_answer_for_problem(answer: dict, problem: Problem,
                                allow_partial: bool = False) -> None:
    """Prove output completeness, validity, and exact source-line citations."""
    try:
        validate_answer(answer)
    except ValueError as exc:
        raise AnswerValidationError(str(exc)) from exc
    schedules = reference_schedules(problem)
    expected = {_signature(schedule) for schedule in schedules}
    case = answer["case"]
    if case == "inconsistent":
        if schedules:
            raise AnswerValidationError("An inconsistent answer has valid schedules")
        sources = {rule.source_line: rule.source for rule in problem.constraints}
        source_lines = {}
        for number, source in sources.items():
            source_lines.setdefault(source, []).append(number)
        citations = answer["conflicts"]
        if (not citations or len(citations) != len(set(citations))
                or any(source not in source_lines for source in citations)):
            raise AnswerValidationError("Conflict citations must be distinct exact source lines")
        # Identical source text can occur on different lines. Accept a citation
        # only if some distinct-line interpretation forms a minimal conflict.
        for selected in itertools.product(*(source_lines[source] for source in citations)):
            core = set(selected)
            if len(core) != len(citations) or reference_schedules(problem, core):
                continue
            if all(reference_schedules(problem, core - {number}) for number in core):
                return
        raise AnswerValidationError("Conflict citations are not a deletion-minimal contradiction")
    assignments = [answer["assignment"]] if case == "unique" else answer["assignments"]
    actual = {_signature(assignment) for assignment in assignments}
    if allow_partial:
        if case != "ambiguous" or len(expected) <= 4 or not actual <= expected:
            raise AnswerValidationError("Partial answer contains an invalid schedule")
    elif (case == "unique" and len(expected) != 1) or (
        case == "ambiguous" and not 2 <= len(expected) <= 4
    ) or actual != expected:
        raise AnswerValidationError("Answer omits or adds a valid schedule, person, or assignment")
