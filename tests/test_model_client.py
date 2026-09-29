import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.model_client import BudgetExceeded, GraniteClient, ModelConfig, ModelRequestError


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "calls.jsonl"
        self.config = ModelConfig("private-test-key", "https://example.test/v1")

    def client(self, budget="1x"):
        return GraniteClient(self.config, budget, self.path)

    @staticmethod
    def response():
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "id": "test", "choices": [{"message": {"content": '{"status":"ok"}'}}]
        }).encode()
        return response

    @patch("src.model_client.urllib.request.urlopen")
    def test_settings_and_1x(self, send):
        send.return_value = self.response()
        client = self.client()
        client.complete("TEST-001", [{"role": "user", "content": "hello"}])
        request = send.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], "ibm-granite/granite-4.2-8b")
        self.assertEqual(payload["temperature"], 1.0)
        self.assertEqual(payload["top_p"], 0.95)
        self.assertEqual(payload["reasoning"], {"enabled": False})
        self.assertEqual(dict(request.header_items())["X-item-id"], "TEST-001")
        with self.assertRaises(BudgetExceeded):
            client.complete("TEST-001", [{"role": "user", "content": "hello"}])
        self.assertEqual(send.call_count, 1)
        self.assertEqual(client.call_counts, {"TEST-001": 1})
        self.assertNotIn("private-test-key", self.path.read_text())
        self.assertIn('"event": "response"', self.path.read_text())

    @patch("src.model_client.urllib.request.urlopen", side_effect=TimeoutError)
    def test_no_retry(self, send):
        client = self.client()
        with self.assertRaises(ModelRequestError):
            client.complete("TEST-001", [{"role": "user", "content": "hello"}])
        with self.assertRaises(BudgetExceeded):
            client.complete("TEST-001", [{"role": "user", "content": "hello"}])
        self.assertEqual(send.call_count, 1)

    @patch("src.model_client.urllib.request.urlopen")
    def test_per_item_limits(self, send):
        send.return_value = self.response()
        client = self.client("3x")
        for _ in range(3):
            client.complete("A", [{"role": "user", "content": "hello"}])
        client.complete("B", [{"role": "user", "content": "hello"}])
        client.assert_budget_compliance(["A", "B"])
        with self.assertRaises(BudgetExceeded):
            client.complete("A", [{"role": "user", "content": "hello"}])
        with self.assertRaises(BudgetExceeded):
            client.assert_budget_compliance(["C"])

    def test_wrong_model(self):
        with self.assertRaises(ValueError):
            ModelConfig.from_env({"OPENROUTER_API_KEY": "key",
                                 "OPENROUTER_BASE_URL": "https://example.test/v1",
                                 "MODEL": "other-model"})

    @patch("src.model_client.urllib.request.urlopen")
    def test_10x_limit(self, send):
        send.return_value = self.response()
        client = self.client("10x")
        for _ in range(10):
            client.complete("A", [{"role": "user", "content": "hello"}])
        client.assert_budget_compliance(["A"])
        with self.assertRaises(BudgetExceeded):
            client.complete("A", [{"role": "user", "content": "hello"}])
        self.assertEqual(send.call_count, 10)


if __name__ == "__main__":
    unittest.main()
