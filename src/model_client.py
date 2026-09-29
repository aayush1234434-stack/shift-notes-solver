from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

MODEL = "ibm-granite/granite-4.2-8b"
BUDGETS = {"1x": 1, "3x": 3, "10x": 10}


class BudgetExceeded(RuntimeError):
    pass


class ModelRequestError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelConfig:
    api_key: str
    base_url: str
    timeout: float = 90.0

    @classmethod
    def from_env(cls, values: Mapping[str, str] | None = None) -> ModelConfig:
        env = os.environ if values is None else values
        key = env.get("OPENROUTER_API_KEY", "").strip()
        base = env.get("OPENROUTER_BASE_URL", "").strip().rstrip("/")
        if not key or not base:
            raise ValueError("Set OPENROUTER_API_KEY and OPENROUTER_BASE_URL in .env.")
        if not base.startswith("https://"):
            raise ValueError("The provided endpoint must use HTTPS.")
        if env.get("MODEL", MODEL).strip() != MODEL:
            raise ValueError(f"Only {MODEL} is permitted.")
        return cls(api_key=key, base_url=base)


class GraniteClient:
    # Plain urllib on purpose: the OpenAI SDK retries on its own, and a retry
    # is a second call the proxy counts against the item's budget.

    def __init__(self, config: ModelConfig, budget: str, log_path: Path):
        if budget not in BUDGETS:
            raise ValueError(f"Unsupported budget: {budget}")
        self.config = config
        self.budget = budget
        self.limit = BUDGETS[budget]
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    @property
    def call_counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)

    def _record(self, event: dict) -> None:
        event = {"timestamp": datetime.now(timezone.utc).isoformat(), **event}
        with self.log_path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(event, ensure_ascii=False) + "\n")

    def complete(self, item_id: str, messages: list[dict[str, str]]) -> str:
        if not item_id:
            raise ValueError("Every call needs an item id for X-Item-Id.")
        body = json.dumps({
            "model": MODEL,
            "messages": messages,
            "temperature": 1.0,
            "top_p": 0.95,
            "reasoning": {"enabled": False},
            "stream": False,
        }).encode("utf-8")
        request = urllib.request.Request(
            self.config.base_url + "/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
                "X-Item-Id": item_id,
            },
            method="POST",
        )

        with self._lock:
            count = self._counts.get(item_id, 0)
            if count >= self.limit:
                raise BudgetExceeded(f"{item_id}: {self.budget} limit already reached.")
            call_number = count + 1
            # Count the call before sending. A timeout may still have reached the proxy.
            self._record({"event": "attempt", "item_id": item_id,
                          "call_number": call_number, "budget": self.budget,
                          "model": MODEL})
            self._counts[item_id] = call_number

        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            content = payload["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("Missing text response")
        except Exception as exc:
            # Do not log request headers, credentials, or raw error bodies.
            status = getattr(exc, "code", None)
            with self._lock:
                self._record({"event": "failure", "item_id": item_id,
                              "call_number": call_number,
                              "error_type": type(exc).__name__,
                              "http_status": status})
            detail = f"HTTP {status}" if status is not None else type(exc).__name__
            raise ModelRequestError(f"{item_id}: call {call_number} failed ({detail})") from None

        with self._lock:
            self._record({"event": "response", "item_id": item_id,
                          "call_number": call_number, "content": content,
                          "usage": payload.get("usage"),
                          "response_id": payload.get("id")})
        return content

    def assert_budget_compliance(self, item_ids: list[str]) -> None:
        counts = self.call_counts
        for item_id in item_ids:
            count = counts.get(item_id, 0)
            if not 1 <= count <= self.limit:
                raise BudgetExceeded(f"{item_id}: invalid call count {count}.")
