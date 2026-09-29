import z3

from .constraint_compiler import compile_problem
from .schema import Problem


class SolverError(RuntimeError):
    pass


class ExtractionIncompleteError(SolverError):
    pass


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

    def minimal_conflict(self, prefer_lines=None) -> list[int]:
        # Deletion-minimal, not smallest: drop one line at a time while it stays unsat.
        # prefer_lines are tried last, so they tend to survive into the citation.
        if self.is_satisfiable():
            raise SolverError("A satisfiable problem has no conflicting set")
        core = sorted(self.compiled.statements)
        prefer = set(prefer_lines or ())
        ordered = sorted(core, key=lambda number: (number in prefer, number))
        for number in ordered:
            trial = [line for line in core if line != number]
            if trial and not self.is_satisfiable(trial):
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

    def result(self, prefer_lines=None) -> dict:
        solutions = self.all_solutions()
        if not solutions:
            core = self.minimal_conflict(prefer_lines)
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
