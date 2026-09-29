import copy
import json
import unittest
from pathlib import Path

from src.answer_validation import (AnswerValidationError, reference_schedules,
                                   validate_answer_for_problem)
from src.schema import parse_problem
from src.solver import solve_problem

ROOT = Path(__file__).resolve().parents[1]


def example(case):
    data = json.loads((ROOT / "examples" / f"{case}.json").read_text())
    notes = (ROOT / "examples" / f"{case}.txt").read_text()
    problem = parse_problem(data, notes)
    return problem, solve_problem(problem)


class AnswerValidationTests(unittest.TestCase):
    def test_all_three_solver_outputs_pass_independent_check(self):
        for case in ("unique", "ambiguous", "inconsistent"):
            with self.subTest(case=case):
                problem, answer = example(case)
                validate_answer_for_problem(answer, problem)

    def test_missing_person_wrong_station_and_invalid_block_fail(self):
        problem, answer = example("unique")
        for mutation in (
            lambda a: a["assignment"].pop("Bob"),
            lambda a: a["assignment"]["Bob"].update(station="intake"),
            lambda a: a["assignment"]["Alice"].update(block="09:00"),
        ):
            damaged = copy.deepcopy(answer)
            mutation(damaged)
            with self.assertRaises(AnswerValidationError):
                validate_answer_for_problem(damaged, problem)

    def test_missing_ambiguous_schedule_fails(self):
        problem, answer = example("ambiguous")
        answer["assignments"].pop()
        with self.assertRaises(ValueError):
            validate_answer_for_problem(answer, problem)

    def test_conflict_must_be_exact_and_deletion_minimal(self):
        problem, answer = example("inconsistent")
        for citations in ([], answer["conflicts"][:-1],
                          answer["conflicts"] + [problem.constraints[-1].source],
                          [answer["conflicts"][0].rstrip(".")] + answer["conflicts"][1:]):
            with self.subTest(citations=citations), self.assertRaises(AnswerValidationError):
                validate_answer_for_problem({"case": "inconsistent", "conflicts": citations}, problem)

    def test_reference_enumerator_includes_station_variants(self):
        problem, _ = example("ambiguous")
        schedules = reference_schedules(problem)
        self.assertEqual(len(schedules), 2)
        self.assertTrue(all(set(schedule) == set(problem.people) for schedule in schedules))
