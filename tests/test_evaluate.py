import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.evaluate import evaluate, model_calls_from_stdout, render_report
from src.extractor import build_messages, decode_extraction
from src.pipeline import answer_from_problem, run_pipeline
from tests.test_pipeline import SequenceClient, invented_item


class AblationTests(unittest.TestCase):
    def test_call_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "summary.json").write_text(json.dumps({
                "call_counts": {"ID-1": 2, "ID-2": 1}}))
            self.assertEqual(model_calls_from_stdout(f"Answers: x\nEvidence: {root}\n"), 3)
            self.assertIsNone(model_calls_from_stdout("No evidence"))

    def test_no_examples(self):
        item, _ = invented_item("unique")
        full = build_messages(item)[0]["content"]
        ablated = build_messages(item, prompt_examples=False)[0]["content"]
        self.assertIn("Generic examples", full)
        self.assertNotIn("Generic examples", ablated)
        self.assertIn("Extract every definite constraint", ablated)
        self.assertEqual(build_messages(item)[1], build_messages(item, False)[1])

    def test_no_source_check(self):
        item, data = invented_item("unique")
        data["constraints"][0]["source"] = "Invented quote."
        from src.schema import SchemaError
        with self.assertRaises(SchemaError):
            decode_extraction(json.dumps(data), item)
        problem = decode_extraction(json.dumps(data), item, source_validation=False)
        self.assertEqual(problem.constraints[0].source, "Invented quote.")

    def test_review_switches(self):
        item, data = invented_item("unique")
        for budget, ablation, expected in (("3x", "extraction_review", 1),
                                           ("10x", "sentence_decomposition", 2)):
            with self.subTest(ablation=ablation), tempfile.TemporaryDirectory() as directory:
                from tests.test_pipeline import selection_text
                responses = [selection_text(item), "no changes"]
                client = SequenceClient(budget, responses)
                out = Path(directory) / "answers.json"
                run_pipeline([item], client, Path(directory) / "run", out, budget, ablation)
                self.assertEqual(client.call_counts[item["id"]], expected)
                self.assertEqual(json.loads(out.read_text())[item["id"]]["case"], "unique")

    def test_no_core_search(self):
        item, data = invented_item("inconsistent")
        problem = decode_extraction(json.dumps(data), item)
        self.assertEqual(answer_from_problem(problem)[0]["case"], "inconsistent")
        answer, status, _ = answer_from_problem(problem, minimal_conflict_search=False)
        self.assertEqual(status, "core_search_ablated")
        self.assertEqual(answer, {"case": "inconsistent", "conflicts": []})

    @patch("src.evaluate.subprocess.run")
    def test_report(self, runner):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            items, key, scorer = [root / name for name in ("items.json", "key.json", "score.py")]
            items.write_text('[{"id": "TEST-001", "text": "invented"}]')
            key.write_text('{"TEST-001": {"case": "unique"}}')
            scorer.write_text("fixture")
            score = {"macro_exact_match": 0.25,
                     "per_case_rate": {case: 0.25 for case in ("unique", "ambiguous", "inconsistent")},
                     "confusion": {true: {declared: 1 for declared in
                         ("unique", "ambiguous", "inconsistent", "unparsed")}
                         for true in ("unique", "ambiguous", "inconsistent")}}

            def respond(command, **kwargs):
                if "--budget" in command:
                    return type("Result", (), {"returncode": 0, "stdout": "pipeline ok", "stderr": ""})()
                return type("Result", (), {"returncode": 0, "stdout": json.dumps(score), "stderr": ""})()

            runner.side_effect = respond
            result = evaluate(items, key, scorer, root / "eval", ["1x"],
                              ["full", "prompt_examples"], 2)
            self.assertEqual(result["completed"], 4)
            report = (root / "eval" / "ablation_table.md").read_text()
            self.assertIn("25.0% (n=2/2)", report)
            self.assertIn("| unique | 2 | 2 | 2 | 2 |", report)
            commands = [call.args[0] for call in runner.call_args_list
                        if "--budget" in call.args[0]]
            self.assertEqual(sum("--ablate" in command for command in commands), 2)

    @patch("src.evaluate.subprocess.run")
    def test_failed_endpoint(self, runner):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            items, key, scorer = [root / name for name in ("items.json", "key.json", "score.py")]
            items.write_text('[{"id": "TEST-001", "text": "invented"}]')
            key.write_text('{"TEST-001": {"case": "unique"}}')
            scorer.write_text("fixture")
            runner.return_value = type("Result", (), {
                "returncode": 1, "stdout": "", "stderr": "HTTP 402"})()
            result = evaluate(items, key, scorer, root / "eval", ["1x", "3x", "10x"],
                              ["full", "source_validation"], 1)
            self.assertTrue(result["failed"])
            self.assertEqual(result["completed"], 0)
            self.assertEqual(runner.call_count, 1)
            report = (root / "eval" / "ablation_table.md").read_text()
            self.assertIn("unmeasured (n=0/1)", report)
            self.assertNotIn("0.0% (n=", report)
            self.assertIn("HTTP 402", report)

    @patch("src.evaluate.subprocess.run")
    def test_shards(self, runner):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            items, key, scorer = [root / name for name in ("items.json", "key.json", "score.py")]
            entries = [{"id": f"{case}-{index}", "text": "invented"}
                       for case in ("unique", "ambiguous", "inconsistent") for index in range(2)]
            items.write_text(json.dumps(entries))
            key.write_text(json.dumps({item["id"]: {"case": item["id"].split("-")[0]}
                                       for item in entries}))
            scorer.write_text("fixture")
            score = {"macro_exact_match": 0.5,
                     "per_case_rate": {case: 0.5 for case in ("unique", "ambiguous", "inconsistent")},
                     "confusion": {case: {declared: 0 for declared in
                         ("unique", "ambiguous", "inconsistent", "unparsed")}
                         for case in ("unique", "ambiguous", "inconsistent")}}

            def respond(command, **kwargs):
                if "--budget" in command:
                    selected = json.loads(Path(command[1]).read_text())
                    Path(command[command.index("--out") + 1]).write_text(json.dumps({
                        item["id"]: {"case": "unique"} for item in selected}))
                    return type("Result", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()
                answers = json.loads(Path(command[3]).read_text())
                self.assertEqual(len(answers), 3)
                return type("Result", (), {"returncode": 0,
                                             "stdout": json.dumps(score), "stderr": ""})()

            runner.side_effect = respond
            result = evaluate(items, key, scorer, root / "eval", ["1x"], ["full"], 1,
                              per_case=1, seed=9, shards=3)
            self.assertEqual(result["completed"], 1)
            manifest = json.loads((root / "eval" / "manifest.json").read_text())
            self.assertEqual(manifest["n_items"], 3)
            self.assertEqual({key_id.split("-")[0] for key_id in manifest["selected_ids"]},
                             {"unique", "ambiguous", "inconsistent"})
            self.assertIn("balanced pilot", (root / "eval" / "ablation_table.md").read_text())

    @patch("src.evaluate.subprocess.run")
    def test_timeout(self, runner):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            items, key, scorer = [root / name for name in ("items.json", "key.json", "score.py")]
            items.write_text('[{"id": "TEST-001", "text": "invented"}]')
            key.write_text('{"TEST-001": {"case": "unique"}}')
            scorer.write_text("fixture")
            runner.side_effect = subprocess.TimeoutExpired("run", 1)
            result = evaluate(items, key, scorer, root / "eval", ["1x"], ["full"], 1,
                              timeout_seconds=1)
            self.assertTrue(result["failed"])
            report = (root / "eval" / "ablation_table.md").read_text()
            self.assertIn("unmeasured (n=0/1)", report)
            self.assertIn("Exceeded 1s subprocess timeout", report)


if __name__ == "__main__":
    unittest.main()
