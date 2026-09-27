import copy
import itertools
import json
import unittest
from pathlib import Path

from src.schema import SchemaError, parse_problem
from src.solver import ExtractionIncompleteError, ScheduleSolver, SolverError, solve_problem

ROOT = Path(__file__).resolve().parents[1]


def example(name):
    data = json.loads((ROOT / "examples" / f"{name}.json").read_text())
    notes = (ROOT / "examples" / f"{name}.txt").read_text()
    return data, notes


def signature(assignment):
    return json.dumps(assignment, sort_keys=True)


class SolverTests(unittest.TestCase):
    def test_unique_example_preserves_names_and_omits_nonholder_station(self):
        data, notes = example("unique")
        answer = solve_problem(parse_problem(data, notes))
        self.assertEqual(answer, {"case": "unique", "assignment": {
            "Alice": {"block": "07:00", "station": "intake"},
            "Bob": {"block": "09:00"},
            "Carla": {"block": "11:00", "station": "packing"}}})

    def test_ambiguous_example_returns_both_complete_schedules(self):
        data, notes = example("ambiguous")
        answer = solve_problem(parse_problem(data, notes))
        self.assertEqual(answer["case"], "ambiguous")
        self.assertEqual(len(answer["assignments"]), 2)
        self.assertEqual({(a["Bob"]["block"], a["Carla"]["block"])
                          for a in answer["assignments"]},
                         {("09:00", "11:00"), ("11:00", "09:00")})

    def test_inconsistent_example_removes_irrelevant_rule_and_proves_minimality(self):
        data, notes = example("inconsistent")
        solver = ScheduleSolver(parse_problem(data, notes))
        core = solver.minimal_conflict()
        self.assertEqual(core, [2, 3, 4])
        self.assertFalse(solver.is_satisfiable(core))
        for number in core:
            self.assertTrue(solver.is_satisfiable([x for x in core if x != number]))
        self.assertEqual(solver.result(), {"case": "inconsistent",
            "conflicts": [rule["source"] for rule in data["constraints"][:3]]})

    def test_station_only_ambiguity_is_not_lost(self):
        data, _ = example("unique")
        data["constraints"] = data["constraints"][:2]
        answer = solve_problem(parse_problem(data))
        self.assertEqual(answer["case"], "ambiguous")
        self.assertEqual(len(answer["assignments"]), 2)
        self.assertEqual({a["Alice"]["station"] for a in answer["assignments"]},
                         {"intake", "packing"})

    def test_background_enumerates_all_720_models_without_duplicates(self):
        problem = parse_problem({"people": ["A", "B", "C", "D", "E"],
            "blocks": ["07:00", "09:00", "11:00", "13:00", "15:00"],
            "stations": ["intake", "packing", "calibration"],
            "station_holders": ["A", "C", "E"], "constraints": []})
        solutions = ScheduleSolver(problem).all_solutions()
        self.assertEqual(len(solutions), 720)
        self.assertEqual(len({signature(a) for a in solutions}), 720)
        for assignment in solutions:
            self.assertEqual(len({x["block"] for x in assignment.values()}), 5)
            self.assertEqual(len({assignment[p]["station"] for p in ["A", "C", "E"]}), 3)
            self.assertNotIn("station", assignment["B"])

    def test_too_many_solutions_is_explicit_error_not_truncation(self):
        problem = parse_problem({"people": ["A", "B", "C"], "blocks": ["a", "b", "c"],
                                 "stations": [], "station_holders": [], "constraints": []})
        with self.assertRaises(ExtractionIncompleteError):
            solve_problem(problem)
        self.assertEqual(len(ScheduleSolver(problem).all_solutions()), 6)

    def test_compound_statement_constraints_are_removed_together(self):
        data = {"people": ["Alice", "Bob", "Carla"], "blocks": ["07:00", "09:00", "11:00"],
                "stations": [], "station_holders": [], "constraints": [
            {"type": "fixed_block", "person": "Alice", "block": "07:00",
             "source_line": 1, "source": "Alice works at 07:00 and Bob at 09:00."},
            {"type": "fixed_block", "person": "Bob", "block": "09:00",
             "source_line": 1, "source": "Alice works at 07:00 and Bob at 09:00."},
            {"type": "not_block", "person": "Bob", "block": "09:00",
             "source_line": 2, "source": "Bob does not work at 09:00."}]}
        solver = ScheduleSolver(parse_problem(data))
        self.assertEqual(solver.minimal_conflict(), [1, 2])
        self.assertEqual(len(solver.result()["conflicts"]), 2)

    def test_satisfiable_problem_has_no_core(self):
        data, notes = example("unique")
        with self.assertRaises(SolverError):
            ScheduleSolver(parse_problem(data, notes)).minimal_conflict()

    def test_every_rule_matches_independent_exhaustive_reference(self):
        base = {"people": ["A", "B", "C", "D"], "blocks": ["07:00", "09:00", "11:00", "13:00"],
                "stations": ["intake", "packing"], "station_holders": ["A", "B"], "constraints": []}
        # Independent reference evaluates actual permutations, without Z3.
        cases = [
            ("fixed_block", {"block": "09:00"}, lambda p, s: p["A"] == 1),
            ("not_block", {"block": "09:00"}, lambda p, s: p["A"] != 1),
            ("fixed_station", {"station": "packing"}, lambda p, s: s["A"] == "packing"),
            ("not_station", {"station": "packing"}, lambda p, s: s["A"] != "packing"),
            ("before", {"other_person": "B"}, lambda p, s: p["A"] < p["B"]),
            ("after", {"other_person": "B"}, lambda p, s: p["A"] > p["B"]),
            ("immediately_before", {"other_person": "B"}, lambda p, s: p["B"] - p["A"] == 1),
            ("immediately_after", {"other_person": "B"}, lambda p, s: p["A"] - p["B"] == 1),
            ("adjacent", {"other_person": "B"}, lambda p, s: abs(p["A"] - p["B"]) == 1),
            ("not_adjacent", {"other_person": "B"}, lambda p, s: abs(p["A"] - p["B"]) != 1),
            ("between", {"other_person": "B", "third_person": "C"},
             lambda p, s: min(p["B"], p["C"]) < p["A"] < max(p["B"], p["C"]))]
        for kind, fields, predicate in cases:
            with self.subTest(kind=kind):
                data = copy.deepcopy(base)
                data["constraints"] = [{"type": kind, "person": "A", **fields,
                                         "source_line": 1, "source": "Invented test statement."}]
                actual = {signature(a) for a in ScheduleSolver(parse_problem(data)).all_solutions()}
                expected = set()
                for order in itertools.permutations(range(4)):
                    positions = dict(zip(base["people"], order))
                    for stations in itertools.permutations(base["stations"]):
                        station_map = dict(zip(base["station_holders"], stations))
                        if predicate(positions, station_map):
                            assignment = {p: {"block": base["blocks"][positions[p]]} for p in base["people"]}
                            for person, station in station_map.items():
                                assignment[person]["station"] = station
                            expected.add(signature(assignment))
                self.assertEqual(actual, expected)


class SchemaTests(unittest.TestCase):
    def test_exact_source_reference_and_blank_line_numbering(self):
        data, notes = example("unique")
        parse_problem(data, notes.replace("\n", "\n\n"))
        data["constraints"][0]["source"] = "Alice works at 07:00"
        with self.assertRaises(SchemaError):
            parse_problem(data, notes)

    def test_rejects_unknown_entities_unexpected_fields_and_bad_headers(self):
        original, _ = example("unique")
        mutations = [
            lambda d: d["people"].append("Alice"),
            lambda d: d["blocks"].pop(),
            lambda d: d["station_holders"].append("Bob"),
            lambda d: d["constraints"][0].update(person="Ghost"),
            lambda d: d["constraints"][0].update(source_line=True),
            lambda d: d["constraints"][0].update(extra="unexpected"),
            lambda d: d["constraints"][2].update(person="Bob"),
            lambda d: d.update(ignored_lines=[2]),
        ]
        for mutation in mutations:
            data = copy.deepcopy(original)
            mutation(data)
            with self.subTest(data=data), self.assertRaises(SchemaError):
                parse_problem(data)

    def test_same_source_line_cannot_have_different_quotes(self):
        data, _ = example("unique")
        data["constraints"][1]["source_line"] = 2
        with self.assertRaises(SchemaError):
            parse_problem(data)


if __name__ == "__main__":
    unittest.main()
