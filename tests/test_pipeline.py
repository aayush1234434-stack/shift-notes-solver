import copy
import json
import tempfile
import unittest
from pathlib import Path

from src.extractor import (build_messages, decode_extraction, header_from_item,
                           header_line_number, note_line_numbers)
from src.selection import menus_for_item
from src.model_client import ModelRequestError
from src.output_writer import validate_answer
from src.pipeline import answer_from_response, run_pipeline, validate_items
from src.schema import SchemaError

ROOT = Path(__file__).resolve().parents[1]


def selection_text(item, overrides=None):
    """Pick each line's first menu letter, unless overrides names another key."""
    overrides = overrides or {}
    rows = []
    for number, options in menus_for_item(item).items():
        key = overrides.get(number, options[0]["key"] if options else "X")
        rows.append(f"{number} {key}")
    return "\n".join(rows)


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


class SequenceClient:
    """Budget-aware fake that supplies one response per distinct model call."""
    def __init__(self, budget, responses):
        self.budget = budget
        self.responses = iter(responses)
        self.call_counts = {}
        self.prompts = []

    def complete(self, item_id, messages):
        self.call_counts[item_id] = self.call_counts.get(item_id, 0) + 1
        assert self.call_counts[item_id] <= {"1x": 1, "3x": 3, "10x": 10}[self.budget]
        self.prompts.append(messages)
        response = next(self.responses)
        return response(messages) if callable(response) else response

    def assert_budget_compliance(self, item_ids):
        assert set(self.call_counts) == set(item_ids)
        assert all(1 <= self.call_counts[item_id] <= {"1x": 1, "3x": 3, "10x": 10}[self.budget]
                   for item_id in item_ids)


class PipelineTests(unittest.TestCase):
    def test_header_not_sent_as_note(self):
        item, _ = invented_item("unique")
        self.assertEqual(header_from_item(item)["station_holders"], ["Alice", "Carla"])
        message = build_messages(item)[1]["content"]
        self.assertIn('"people": ["Alice", "Bob", "Carla"]', message)
        self.assertIn("2: Alice works at 07:00.", message)
        self.assertNotIn("1: Staff:", message)

    def test_visible_header(self):
        item = {"text": "Shift notes, Bay 4. 3 staff on the rota: Alice, Bob, Carla. "
                "Blocks run 07:00, 09:00, 11:00, one person per block, and each person "
                "works exactly one block. There are 2 stations, one person on each: "
                "intake, packing. The people on a station are Alice, Carla; the rest "
                "are on no station.\n\nAlice works at 07:00.",
                "n_staff": 3, "n_stations": 2}
        self.assertEqual(header_from_item(item), {
            "people": ["Alice", "Bob", "Carla"],
            "blocks": ["07:00", "09:00", "11:00"],
            "stations": ["intake", "packing"],
            "station_holders": ["Alice", "Carla"]})

    def test_header_after_note(self):
        item, data = invented_item("unique")
        item["text"] = "The printer was moved.\n" + item["text"]
        for rule in data["constraints"]:
            rule["source_line"] += 1
        data["ignored_lines"] = [1]
        self.assertEqual(header_line_number(item), 2)
        self.assertEqual(note_line_numbers(item), [1, 3, 4, 5])
        self.assertIn("1: The printer was moved.", build_messages(item)[1]["content"])
        self.assertNotIn("2: Staff:", build_messages(item)[1]["content"])
        self.assertEqual(len(decode_extraction(json.dumps(data), item).constraints), 3)

    def test_bad_headers(self):
        item, _ = invented_item("unique")
        header = item["text"].splitlines()[0]
        for text in (header + " Extra text.\nAlice works at 07:00.",
                     header + "\n" + header + "\nAlice works at 07:00."):
            with self.subTest(text=text), self.assertRaisesRegex(SchemaError, "header line"):
                header_from_item({**item, "text": text})
        exact = {**item, "text": header.replace("Alice", "ALIce") + "\nALIce works at 07:00."}
        self.assertEqual(header_from_item(exact)["people"][0], "ALIce")

    def test_short_quote_becomes_full_line(self):
        item, data = invented_item("unique")
        rule = copy.deepcopy(data["constraints"][0])
        rule["source"] = "Alice works at 07:00"
        response = {"constraints": [rule], "ignored_lines": [3, 4]}
        problem = decode_extraction(json.dumps(response), item)
        self.assertEqual(problem.constraints[0].source, "Alice works at 07:00.")
        self.assertEqual(problem.ignored_lines, (3, 4))

    def test_missing_or_overlapping_line(self):
        item, data = invented_item("unique")
        missing = copy.deepcopy(data)
        missing["constraints"] = missing["constraints"][:-1]
        with self.assertRaisesRegex(SchemaError, "Unclassified note lines"):
            decode_extraction(json.dumps(missing), item)
        overlapping = copy.deepcopy(data)
        overlapping["ignored_lines"] = [2]
        with self.assertRaisesRegex(SchemaError, "both constrained and ignored"):
            decode_extraction(json.dumps(overlapping), item)

    def test_full_run(self):
        items, responses = [], {}
        for case in ("unique", "ambiguous", "inconsistent"):
            item, data = invented_item(case)
            items.append(item)
            responses[item["id"]] = selection_text(item)
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

    def test_output_changes_answer(self):
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

    def test_letter_picks_direction(self):
        item = {"id": "REVERSAL", "n_staff": 2, "n_stations": 1, "text":
                "Staff: Rohan, Priya. Blocks: 09:00, 11:00, one person each. "
                "Station holders: Rohan. Stations: calibration, one person each.\n"
                "I'm fairly sure Rohan works later than Priya.\n"}
        accepted = FakeClient({item["id"]: selection_text(item)})
        rejected = FakeClient({item["id"]: "2 X"})
        reversed_choice = FakeClient({item["id"]: "2 B"})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_pipeline([item], accepted, root / "a", root / "accepted.json")
            run_pipeline([item], rejected, root / "b", root / "rejected.json")
            run_pipeline([item], reversed_choice, root / "c", root / "reversed.json")
            yes = json.loads((root / "accepted.json").read_text())[item["id"]]
            no = json.loads((root / "rejected.json").read_text())[item["id"]]
            flipped = json.loads((root / "reversed.json").read_text())[item["id"]]
        self.assertEqual(yes["assignment"]["Rohan"]["block"], "11:00")
        self.assertEqual(yes["assignment"]["Priya"]["block"], "09:00")
        self.assertNotEqual(yes, no)
        self.assertEqual(flipped["assignment"]["Rohan"]["block"], "09:00")
        self.assertEqual(flipped["assignment"]["Priya"]["block"], "11:00")

    def test_social_line_menu(self):
        item, _ = invented_item("ambiguous")
        item["text"] += "Alice and Bob car-share to the site.\n"
        menus = menus_for_item(item)
        self.assertEqual(menus[4], [])
        nonholder = {"id": "NONHOLDER", "n_staff": 3, "n_stations": 2, "text":
                     "Staff: Alice, Bob, Carla. Blocks: 07:00, 09:00, 11:00, one person each. "
                     "Station holders: Alice, Carla. Stations: intake, packing, one person each.\n"
                     "Bob mentioned intake during the walkaround.\n"
                     "Bob starts after intake.\n"}
        nonholder_menus = menus_for_item(nonholder)
        self.assertEqual(nonholder_menus[2], [])
        self.assertEqual(
            [rule["type"] for option in nonholder_menus[3] for rule in option["rules"]],
            ["station_before_person", "station_after_person"])
        accepted = FakeClient({item["id"]: selection_text(item)})
        dropped = FakeClient({item["id"]: selection_text(item, {2: "X"})})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_pipeline([item], accepted, root / "a", root / "accepted.json")
            run_pipeline([item], dropped, root / "b", root / "dropped.json")
            yes = json.loads((root / "accepted.json").read_text())[item["id"]]
            no = json.loads((root / "dropped.json").read_text())[item["id"]]
        unseen = {"id": "UNSEEN", "n_staff": 3, "n_stations": 2, "text":
                  "Staff: Alice, Bob, Carla. Blocks: 07:00, 09:00, 11:00, one person each. "
                  "Station holders: Alice, Carla. Stations: intake, packing, one person each.\n"
                  "Alice gets in sooner than Bob.\n"
                  "Intake is underway sooner than Bob.\n"}
        unseen_menus = menus_for_item(unseen)
        self.assertEqual(
            [(rule["type"], rule["person"]) for option in unseen_menus[2] for rule in option["rules"]],
            [("before", "Alice"), ("before", "Bob")])
        self.assertEqual(
            [rule["type"] for option in unseen_menus[3] for rule in option["rules"]],
            ["station_before_person", "station_after_person"])
        self.assertEqual(yes["case"], "ambiguous")
        self.assertEqual(len(yes["assignments"]), 2)
        self.assertNotEqual(yes, no)

    def test_garbage_reply(self):
        item, _ = invented_item("unique")
        client = FakeClient({item["id"]: "not a selection"})
        with tempfile.TemporaryDirectory() as directory:
            run_dir, output = Path(directory) / "run", Path(directory) / "answers.json"
            run_pipeline([item], client, run_dir, output)
            answer = json.loads(output.read_text())[item["id"]]
            self.assertNotEqual(answer.get("case"), "unique")
            self.assertEqual(client.call_counts[item["id"]], 1)

    def test_endpoint_failure(self):
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

    def test_too_many_schedules(self):
        item, data = invented_item("unique")
        data["constraints"] = []
        data["ignored_lines"] = [2, 3, 4]
        answer, status, error = answer_from_response(json.dumps(data), item)
        self.assertEqual(status, "partial_extraction")
        self.assertEqual(answer["case"], "ambiguous")
        self.assertEqual(len(answer["assignments"]), 4)
        self.assertIn("12 solutions", error)
        validate_answer(answer)

    def test_bad_json(self):
        item, data = invented_item("unique")
        bad = copy.deepcopy(data)
        bad["constraints"][0]["source"] = "Made up source."
        for raw in (json.dumps(bad), '{"people": [], "people": []}', '{"people": NaN}'):
            with self.subTest(raw=raw), self.assertRaises(SchemaError):
                decode_extraction(raw, item)

    def test_json_fence(self):
        item, data = invented_item("unique")
        problem = decode_extraction("```json\n" + json.dumps(data) + "\n```", item)
        self.assertEqual(problem.people, ("Alice", "Bob", "Carla"))

    def test_header_count_mismatch(self):
        item, data = invented_item("unique")
        item["n_staff"] = 5
        with self.assertRaises(SchemaError):
            decode_extraction(json.dumps(data), item)

    def test_duplicate_ids(self):
        item, _ = invented_item("unique")
        with self.assertRaises(ValueError):
            validate_items([item, item])

    def test_review_fixes_letter(self):
        item, _ = invented_item("unique")
        client = SequenceClient("3x", [selection_text(item, {3: "B"}), selection_text(item)])
        with tempfile.TemporaryDirectory() as directory:
            run_dir, output = Path(directory) / "run", Path(directory) / "answers.json"
            run_pipeline([item], client, run_dir, output)
            answer = json.loads(output.read_text())[item["id"]]
            self.assertEqual(answer["assignment"]["Carla"]["block"], "11:00")
            records = json.loads((run_dir / "records.json").read_text())
            self.assertEqual([a["kind"] for a in records[0]["attempts"]],
                             ["selection", "selection_review"])
        self.assertEqual(client.call_counts[item["id"]], 2)

    def test_3x_reviews(self):
        item, _ = invented_item("unique")
        client = SequenceClient("3x", [selection_text(item), "no changes"])
        with tempfile.TemporaryDirectory() as directory:
            run_pipeline([item], client, Path(directory) / "run", Path(directory) / "answer.json")
        self.assertEqual(client.call_counts[item["id"]], 2)

    def test_review_after_empty_reply(self):
        item, _ = invented_item("unique")
        client = SequenceClient("3x", ["not a selection", selection_text(item)])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "answer.json"
            run_pipeline([item], client, Path(directory) / "run", output)
            self.assertEqual(json.loads(output.read_text())[item["id"]]["case"], "unique")
        self.assertEqual(client.call_counts[item["id"]], 2)
        self.assertIn("already answered", client.prompts[1][0]["content"])

    def test_10x_cap(self):
        item, _ = invented_item("unique")

        def respond(messages):
            body = messages[1]["content"]
            numbers = [int(number) for number in __import__("re").findall(r"(?m)^Line (\d+):", body)]
            return "\n".join(f"{number} A" if number in {2, 3, 4} else f"{number} X"
                             for number in numbers)

        client = SequenceClient("10x", ["2 X\n3 X\n4 X", "no changes"] + [respond] * 8)
        with tempfile.TemporaryDirectory() as directory:
            run_dir, output = Path(directory) / "run", Path(directory) / "answer.json"
            run_pipeline([item], client, run_dir, output, "10x")
            answer = json.loads(output.read_text())[item["id"]]
            self.assertEqual(answer["case"], "unique")
            record = json.loads((run_dir / "records.json").read_text())[0]
            kinds = [attempt["kind"] for attempt in record["attempts"]]
            self.assertEqual(kinds[0], "selection")
            self.assertIn("selection_review", kinds)
            self.assertIn("batch_reselect", kinds)
            self.assertLessEqual(len(kinds), 10)

    def test_10x_garbage(self):
        item, _ = invented_item("unique")
        client = SequenceClient("10x", ["not a selection"] * 10)
        with tempfile.TemporaryDirectory() as directory:
            run_dir, output = Path(directory) / "run", Path(directory) / "answer.json"
            run_pipeline([item], client, run_dir, output, "10x")
            answer = json.loads(output.read_text())[item["id"]]
            record = json.loads((run_dir / "records.json").read_text())[0]
            self.assertNotEqual(answer.get("case"), "unique")
            self.assertLessEqual(len(record["attempts"]), 10)
            self.assertGreater(len(record["attempts"]), 1)

    def test_off_menu_letter(self):
        item, _ = invented_item("unique")
        client = SequenceClient("3x", [selection_text(item), "3 Z\n3 invented constraint"])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "answer.json"
            run_pipeline([item], client, Path(directory) / "run", output)
            self.assertEqual(json.loads(output.read_text())[item["id"]]["case"], "unique")
        self.assertEqual(client.call_counts[item["id"]], 2)


if __name__ == "__main__":
    unittest.main()
