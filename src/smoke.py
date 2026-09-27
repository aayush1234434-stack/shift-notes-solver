"""One synthetic request to verify Phase 1 infrastructure."""

import argparse
import json
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

from .model_client import GraniteClient, ModelConfig, ModelRequestError

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget", choices=["1x", "3x", "10x"], default="1x")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    load_dotenv(args.env_file, override=False)
    run_id = uuid.uuid4().hex
    item_id = "SMOKE-" + run_id
    log_path = ROOT / "logs" / run_id / "calls.jsonl"
    try:
        client = GraniteClient(ModelConfig.from_env(), args.budget, log_path)
        content = client.complete(item_id, [
            {"role": "system", "content": "Return only a JSON object."},
            {"role": "user", "content": 'Return {"status": "ok"}.'},
        ])
        client.assert_budget_compliance([item_id])
    except (ValueError, ModelRequestError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps({"item_id": item_id, "response": content,
                      "call_counts": client.call_counts,
                      "log_path": str(log_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
