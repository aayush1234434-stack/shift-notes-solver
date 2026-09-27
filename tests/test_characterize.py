import json
import unittest

from src.characterize import inspect_response, select_sample


class CharacterizationTests(unittest.TestCase):
    def test_sample_balanced_reproducible_and_not_first_items_only(self):
        items = [{"id": str(i), "text": "notes"} for i in range(12)]
        cases = ("unique", "ambiguous", "inconsistent")
        key = {str(i): {"case": cases[i // 4]} for i in range(12)}
        sample = select_sample(items, key, 2, 42)
        self.assertEqual(sample, select_sample(items, key, 2, 42))
        self.assertEqual(len({x["id"] for x in sample}), 6)
        for case in cases:
            self.assertEqual(sum(key[x["id"]]["case"] == case for x in sample), 2)

    def test_json_fences_are_reported_not_silently_repaired(self):
        issues = inspect_response('```json\n{}\n```', ["notes"])
        self.assertEqual(issues[0]["kind"], "malformed_json")

    def test_source_and_entity_errors_and_coverage(self):
        data = {"people": ["Alice", "Bob"], "blocks": ["07:00", "09:00"],
                "stations": ["intake"], "station_holders": ["Alice"],
                "constraints": [{"type": "before", "person": "Ghost",
                    "other_person": "Bob", "source_line": 2, "source": "invented"}],
                "ignored_lines": [2]}
        issues = inspect_response(json.dumps(data), [
            "Alice Bob 07:00 09:00 intake", "Alice is before Bob.", "A filler line."])
        kinds = {x["kind"] for x in issues}
        self.assertTrue({"unknown_entity", "source_mismatch", "conflicting_classification",
                         "unaccounted_line"}.issubset(kinds))

    def test_semantic_mistake_not_claimed_as_automatic_detection(self):
        data = {"people": ["Alice", "Bob"], "blocks": ["07:00", "09:00"],
                "stations": ["intake"], "station_holders": ["Alice"],
                "constraints": [{"type": "after", "person": "Alice", "other_person": "Bob",
                    "source_line": 2, "source": "Alice is before Bob."}],
                "ignored_lines": [1]}
        self.assertEqual(inspect_response(json.dumps(data), [
            "Alice Bob 07:00 09:00 intake", "Alice is before Bob."]), [])


if __name__ == "__main__":
    unittest.main()
