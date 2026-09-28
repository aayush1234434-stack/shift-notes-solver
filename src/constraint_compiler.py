"""Compile typed rules into Z3 expressions, retaining statement provenance."""

from dataclasses import dataclass

import z3

from .schema import Constraint, Problem


@dataclass
class CompiledProblem:
    problem: Problem
    blocks: dict
    stations: dict
    background: list
    statements: dict[int, list]
    sources: dict[int, str]

    @property
    def variables(self):
        return list(self.blocks.values()) + list(self.stations.values())

    def solver(self, source_lines=None):
        active = set(self.statements) if source_lines is None else set(source_lines)
        if not active <= self.statements.keys():
            raise ValueError("Unknown source line in active constraint subset")
        solver = z3.Solver()
        solver.add(*self.background)
        for number in sorted(active):
            solver.add(*self.statements[number])
        return solver


def compile_rule(rule: Constraint, blocks: dict, stations: dict, problem: Problem):
    a = blocks[rule.person]
    kind = rule.type
    if kind in {"station_before_person", "station_after_person"}:
        station_index = problem.stations.index(rule.station)
        holder_block = z3.Sum([
            z3.If(stations[holder] == station_index, blocks[holder], 0)
            for holder in problem.station_holders
        ])
        return holder_block < a if kind == "station_before_person" else holder_block > a
    if kind == "fixed_block":
        return a == problem.blocks.index(rule.block)
    if kind == "not_block":
        return a != problem.blocks.index(rule.block)
    if kind in {"fixed_station", "not_station"}:
        expression = stations[rule.person] == problem.stations.index(rule.station)
        return expression if kind == "fixed_station" else z3.Not(expression)
    b = blocks[rule.other_person]
    if kind == "before":
        return a < b
    if kind == "after":
        return a > b
    if kind == "immediately_before":
        return a + 1 == b
    if kind == "immediately_after":
        return a == b + 1
    if kind in {"adjacent", "not_adjacent"}:
        expression = z3.Or(a + 1 == b, b + 1 == a)
        return expression if kind == "adjacent" else z3.Not(expression)
    if kind == "between":
        c = blocks[rule.third_person]
        return z3.Or(z3.And(b < a, a < c), z3.And(c < a, a < b))
    raise ValueError(f"Unsupported rule: {kind}")


def compile_problem(problem: Problem) -> CompiledProblem:
    blocks = {person: z3.Int(f"block_{index}") for index, person in enumerate(problem.people)}
    stations = {person: z3.Int(f"station_{index}") for index, person in enumerate(problem.station_holders)}
    background = []
    for variable in blocks.values():
        background.extend([variable >= 0, variable < len(problem.blocks)])
    for variable in stations.values():
        background.extend([variable >= 0, variable < len(problem.stations)])
    if len(blocks) > 1:
        background.append(z3.Distinct(*blocks.values()))
    if len(stations) > 1:
        background.append(z3.Distinct(*stations.values()))
    statements, sources = {}, {}
    for rule in problem.constraints:
        statements.setdefault(rule.source_line, []).append(compile_rule(rule, blocks, stations, problem))
        sources[rule.source_line] = rule.source
    return CompiledProblem(problem, blocks, stations, background, statements, sources)
