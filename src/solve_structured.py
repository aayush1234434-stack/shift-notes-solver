"""Phase 3 CLI for one structured problem, without any model calls."""

import argparse
import json
import sys
from pathlib import Path

from .schema import SchemaError, parse_problem
from .solver import SolverError, solve_problem


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="One extraction JSON object")
    parser.add_argument("--notes", type=Path, help="Original notes for exact source verification")
    parser.add_argument("--out", type=Path, help="Optional output path; otherwise print JSON")
    args = parser.parse_args()
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        notes = args.notes.read_text(encoding="utf-8") if args.notes else None
        result = solve_problem(parse_problem(data, notes))
    except (OSError, ValueError, SchemaError, SolverError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
    else:
        print(output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
