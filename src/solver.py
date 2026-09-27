"""Exact schedule enumeration and deletion-minimal conflicts; no LLM calls."""

import z3

from .constraint_compiler import compile_problem
from .schema import Problem


class SolverError(RuntimeError):
    pass


class ExtractionIncompleteError(SolverError):
    """The extraction has more solutions than the assignment permits."""


def check(solver) -> bool:
    result = solver.check()
    if result == z3.unknown:
        raise SolverError(f"Solver returned unknown: {solver.reason_unknown()}")
    return result == z3.sat


class ScheduleSolver:
    def __init__(self, problem: Problem):
        self.compiled = compile_problem(problem)

    def is_satisfiable(self, source_lines=None) -> bool:
        return check(self.compiled.solver(source_lines))

    def all_solutions(self) -> list[dict]:
        compiled = self.compiled
        problem = compiled.problem
        solver = compiled.solver()
        signatures = []
        while check(solver):
            model = solver.model()
            values = tuple(model.eval(variable, model_completion=True).as_long()
                           for variable in compiled.variables)
            signatures.append(values)
            # Block the entire schedule, including station assignments. Blocking
            # just time variables would silently lose station-only ambiguity.
            solver.add(z3.Or(*(variable != value for variable, value
                               in zip(compiled.variables, values))))
        solutions = []
        for values in sorted(signatures):
            assignment = {person: {"block": problem.blocks[values[index]]}
                          for index, person in enumerate(problem.people)}
            for index, person in enumerate(problem.station_holders, len(problem.people)):
                assignment[person]["station"] = problem.stations[values[index]]
            solutions.append(assignment)
        return solutions

    def minimal_conflict(self) -> list[int]:
        """Shrink complete source statements, keeping header rules permanent.

        Minimal means no member can be removed, not globally smallest size.
        Multiple extracted rules from one sentence are removed as a group.
        """
        if self.is_satisfiable():
            raise SolverError("A satisfiable problem has no conflicting set")
        core = sorted(self.compiled.statements)
        for number in tuple(core):
            trial = [line for line in core if line != number]
            if not self.is_satisfiable(trial):
                core = trial
        if not self.validate_conflict(core):
            raise SolverError("Failed to establish a minimal source conflict")
        return core

    def validate_conflict(self, source_lines: list[int]) -> bool:
        if not source_lines or len(source_lines) != len(set(source_lines)):
            return False
        if self.is_satisfiable(source_lines):
            return False
        return all(self.is_satisfiable([other for other in source_lines if other != number])
                   for number in source_lines)

    def result(self) -> dict:
        solutions = self.all_solutions()
        if not solutions:
            core = self.minimal_conflict()
            return {"case": "inconsistent",
                    "conflicts": [self.compiled.sources[number] for number in core]}
        if len(solutions) == 1:
            return {"case": "unique", "assignment": solutions[0]}
        if len(solutions) <= 4:
            return {"case": "ambiguous", "assignments": solutions}
        raise ExtractionIncompleteError(
            f"Found {len(solutions)} solutions; the assignment permits at most four. "
            "Review extraction instead of truncating the solution set.")


def solve_problem(problem: Problem) -> dict:
    return ScheduleSolver(problem).result()
