import copy
import json
import tempfile
import unittest
from pathlib import Path

from src.extractor import decode_extraction
from src.model_client import ModelRequestError
from src.output_writer import validate_answer
from src.pipeline import answer_from_response, run_pipeline, validate_items
from src.schema import SchemaError

ROOT = Path(__file__).resolve().parents[1]


def invented_item(case):
    data = json.loads((ROOT / "examples" / f"{case}.json").read_text())
    item = {"id": "INVENTED-" + case, "text": (ROOT / "examples" / f"{case}.txt").read_text(),
            "n_staff": 3, "n_stations": 2}
    return item, data


class FakeClient:
    """Offline stub, never used by production code or visible-set evaluation."""
    def __init__(self, responses):
        self.responses = responses
        self.call_counts = {}

    def complete(self, item_id, messages):
        self.call_counts[item_id] = self.call_counts.get(item_id, 0) + 1
        value = self.responses[item_id]
        if isinstance(value, Exception):
            raise value
        return value

    def assert_budget_compliance(self, item_ids):
        assert self.call_counts == {item_id: 1 for item_id in item_ids}


class PipelineTests(unittest.TestCase):
    def test_complete_run_one_call_each_and_expected_cases(self):
        items, responses = [], {}
        for case in ("unique", "ambiguous", "inconsistent"):
            item, data = invented_item(case)
            items.append(item)
            responses[item["id"]] = json.dumps(data)
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run"
            output = Path(directory) / "answers.json"
            client = FakeClient(responses)
            summary = run_pipeline(items, client, run_dir, output)
            answers = json.loads(output.read_text())
            self.assertEqual({key: answer["case"] for key, answer in answers.items()},
                             {item["id"]: item["id"].split("-")[1] for item in items})
            self.assertEqual(summary["call_counts"], {item["id"]: 1 for item in items})
            records = json.loads((run_dir / "records.json").read_text())
            self.assertTrue(all(record["messages"] and record["response"] for record in records))

    def test_changed_model_output_changes_schedule(self):
        item, data = invented_item("unique")
        first, status, _ = answer_from_response(json.dumps(data), item)
        changed = copy.deepcopy(data)
        # Source remains the same; this deliberately simulates semantic model
        # error. The resulting answer must change, demonstrating dependency.
        changed["constraints"][1]["type"] = "before"
        second, _, _ = answer_from_response(json.dumps(changed), item)
        self.assertEqual(status, "solved_from_extraction")
        self.assertNotEqual(first, second)
        self.assertEqual(second["assignment"]["Carla"]["block"], "09:00")

    def test_invalid_extraction_is_logged_without_second_call(self):
        item, _ = invented_item("unique")
        client = FakeClient({item["id"]: "not JSON"})
        with tempfile.TemporaryDirectory() as directory:
            run_dir, output = Path(directory) / "run", Path(directory) / "answers.json"
            run_pipeline([item], client, run_dir, output)
            self.assertEqual(json.loads(output.read_text())[item["id"]],
                             {"case": "inconsistent", "conflicts": []})
            record = json.loads((run_dir / "records.json").read_text())[0]
            self.assertEqual(record["status"], "unresolved")
            self.assertEqual(client.call_counts[item["id"]], 1)

    def test_endpoint_failure_stops_and_does_not_overwrite_output(self):
        first, data = invented_item("unique")
        second, _ = invented_item("ambiguous")
        client = FakeClient({first["id"]: ModelRequestError("HTTP 402")})
        with tempfile.TemporaryDirectory() as directory:
            run_dir, output = Path(directory) / "run", Path(directory) / "answers.json"
            output.write_text("older output")
            with self.assertRaises(ModelRequestError):
                run_pipeline([first, second], client, run_dir, output)
            self.assertEqual(output.read_text(), "older output")
            self.assertEqual(client.call_counts, {first["id"]: 1})
            self.assertEqual(json.loads((run_dir / "records.json").read_text())[0]["status"],
                             "endpoint_failure")

    def test_partial_policy_is_explicit_when_extraction_leaves_many_models(self):
        item, data = invented_item("unique")
        data["constraints"] = []
        answer, status, error = answer_from_response(json.dumps(data), item)
        self.assertEqual(status, "partial_extraction")
        self.assertEqual(answer["case"], "ambiguous")
        self.assertEqual(len(answer["assignments"]), 4)
        self.assertIn("12 solutions", error)
        validate_answer(answer)

    def test_strict_json_and_sources_reject_invalid_model_content(self):
        item, data = invented_item("unique")
        bad = copy.deepcopy(data)
        bad["constraints"][0]["source"] = "Made up source."
        for raw in (json.dumps(bad), '{"people": [], "people": []}', '{"people": NaN}'):
            with self.subTest(raw=raw), self.assertRaises(SchemaError):
                decode_extraction(raw, item)

    def test_exact_outer_fence_is_supported(self):
        item, data = invented_item("unique")
        problem = decode_extraction("```json\n" + json.dumps(data) + "\n```", item)
        self.assertEqual(problem.people, ("Alice", "Bob", "Carla"))

    def test_header_metadata_mismatch_is_rejected(self):
        item, data = invented_item("unique")
        item["n_staff"] = 5
        with self.assertRaises(SchemaError):
            decode_extraction(json.dumps(data), item)

    def test_duplicate_input_ids_are_rejected_before_requests(self):
        item, _ = invented_item("unique")
        with self.assertRaises(ValueError):
            validate_items([item, item])


if __name__ == "__main__":
    unittest.main()
