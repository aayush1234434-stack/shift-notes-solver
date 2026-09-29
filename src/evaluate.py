"""Run official scorer across budgets and single-component ablations."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import random
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from .model_client import BUDGETS, MODEL
from .output_writer import write_json
from .pipeline import ABLATIONS, ROOT

CASES = ("unique", "ambiguous", "inconsistent")
VARIANTS = ("full", *ABLATIONS)


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def percent(value: float) -> str:
    return f"{100 * value:.1f}%"


def score_cell(runs: list[dict]) -> str:
    complete = [run for run in runs if run["status"] == "complete"]
    if not complete:
        return f"unmeasured (n=0/{len(runs)})"
    calls = [run.get("model_calls") for run in complete]
    call_note = f"; mean calls={mean(calls):.1f}" if all(call is not None for call in calls) else ""
    return (f"{percent(mean(run['score']['macro_exact_match'] for run in complete))} "
            f"(n={len(complete)}/{len(runs)}{call_note})")


def model_calls_from_stdout(stdout: str) -> int | None:
    paths = [Path(line.removeprefix("Evidence: ")) for line in stdout.splitlines()
             if line.startswith("Evidence: ")]
    if not paths:
        return None
    total = 0
    for path in paths:
        summary = path / "summary.json"
        if not summary.is_file():
            return None
        data = json.loads(summary.read_text(encoding="utf-8"))
        counts = data.get("call_counts")
        if not isinstance(counts, dict) or not all(type(value) is int for value in counts.values()):
            return None
        total += sum(counts.values())
    return total


def render_report(manifest: dict, results: dict) -> str:
    variants, budgets = manifest["variants"], manifest["budgets"]
    lines = ["# Evaluation ablation results", "",
             f"Model: `{MODEL}`; temperature 1.0, top_p 0.95, reasoning disabled.",
             f"Items per run: {manifest['n_items']} ({manifest['scope']}).",
             f"Input SHA-256: `{manifest['items_sha256']}`; key SHA-256: `{manifest['key_sha256']}`.",
             f"Selection seed: {manifest['seed']}; parallel shards: {manifest['shards']}.",
             f"Planned runs per cell: {manifest['repeats']}. Each score is the mean of completed official-scorer runs.",
             "A missing result is unmeasured, not a zero score.", "",
             "| Component configuration | " + " | ".join(budgets) + " |",
             "|---|" + "---|" * len(budgets)]
    for variant in variants:
        label = "Full system" if variant == "full" else "Without " + variant.replace("_", " ")
        lines.append("| " + label + " | " + " | ".join(
            score_cell(results[variant][budget]) for budget in budgets) + " |")
    lines.extend(["", "## Per-case accuracy and confusion matrices", "",
                  "Rows are true cases; columns are declared unique, ambiguous, inconsistent, unparsed.",
                  "Confusion counts are pooled across completed runs in each cell.", ""])
    for variant in variants:
        for budget in budgets:
            completed = [run for run in results[variant][budget] if run["status"] == "complete"]
            if not completed:
                continue
            lines.append(f"### {variant}, {budget} (n={len(completed)})")
            lines.append("")
            lines.append("Per-case exact rates: " + ", ".join(
                f"{case} {percent(mean(run['score']['per_case_rate'][case] for run in completed))}"
                for case in CASES) + ".")
            lines.extend(["", "| True case | unique | ambiguous | inconsistent | unparsed |",
                          "|---|---:|---:|---:|---:|"])
            for true in CASES:
                counts = [sum(run["score"]["confusion"][true][declared] for run in completed)
                          for declared in (*CASES, "unparsed")]
                lines.append("| " + true + " | " + " | ".join(map(str, counts)) + " |")
            lines.append("")
    failures = [(variant, budget, run) for variant in variants for budget in budgets
                for run in results[variant][budget] if run["status"] == "failed"]
    pending = sum(run["status"] == "not_run" for variant in variants for budget in budgets
                  for run in results[variant][budget])
    running = sum(run["status"] == "running" for variant in variants for budget in budgets
                  for run in results[variant][budget])
    lines.extend(["## Run failures and limits", ""])
    for variant, budget, run in failures:
        lines.append(f"- {variant} {budget} run {run['run_number']}: {run['error']}")
    if pending:
        lines.append(f"- {pending} planned runs have not been attempted yet.")
    if running:
        lines.append(f"- {running} run is in progress; this report is not final.")
    if not failures and not pending and not running:
        lines.append("- All planned runs completed.")
    lines.extend(["", "## Interpretation", "",
                  "Compare each 'Without' row with the Full system at the same budget.",
                  "The 1x review and batch ablations may coincide with Full because those components are inactive at 1x.",
                  "Different calls can sample differently at the required temperature; report the completed run count before attributing a difference to a component.",
                  "The visible set may overstate performance on the held-out wording pool.", ""])
    return "\n".join(lines)


def evaluate(items: Path, key: Path, scorer: Path, out_dir: Path,
             budgets: list[str], variants: list[str], repeats: int,
             env_file: Path | None = None, per_case: int | None = None,
             seed: int = 42, shards: int = 1, timeout_seconds: int = 360) -> dict:
    if repeats < 1 or not budgets or not variants:
        raise ValueError("Provide positive repeats, budgets and variants")
    if len(set(budgets)) != len(budgets) or set(budgets) - BUDGETS.keys():
        raise ValueError("Invalid or duplicate budgets")
    if len(set(variants)) != len(variants) or set(variants) - set(VARIANTS):
        raise ValueError("Invalid or duplicate variants")
    if shards < 1 or timeout_seconds < 1 or (per_case is not None and per_case < 1):
        raise ValueError("Shards, timeout and per-case sample size must be positive")
    for path in (items, key, scorer):
        if not path.is_file():
            raise ValueError(f"Required evaluation file is missing: {path}")
    item_data = json.loads(items.read_text(encoding="utf-8"))
    key_data = json.loads(key.read_text(encoding="utf-8"))
    if not isinstance(item_data, list) or not isinstance(key_data, dict):
        raise ValueError("Items must be an array and the visible key an object")
    ids = [item.get("id") for item in item_data if isinstance(item, dict)]
    if len(ids) != len(item_data) or len(ids) != len(set(ids)) or set(ids) != set(key_data):
        raise ValueError("Items and key must contain the same unique IDs")
    if any(not isinstance(key_data[item_id], dict) or
           key_data[item_id].get("case") not in CASES for item_id in ids):
        raise ValueError("Every key entry must have a recognized case")
    out_dir.mkdir(parents=True, exist_ok=False)
    source_items, source_key = items, key
    if per_case is not None:
        rng = random.Random(seed)
        selected_ids = set()
        for case in CASES:
            group = sorted((item_id for item_id in ids if key_data[item_id]["case"] == case))
            if len(group) < per_case:
                raise ValueError(f"Not enough {case} items for a {per_case}-per-case pilot")
            selected_ids.update(rng.sample(group, per_case))
        item_data = [item for item in item_data if item["id"] in selected_ids]
        key_data = {item["id"]: key_data[item["id"]] for item in item_data}
        items, key = out_dir / "pilot_items.json", out_dir / "pilot_key.json"
        write_json(items, item_data)
        write_json(key, key_data)
    manifest = {
        "created": datetime.now(timezone.utc).isoformat(), "items": str(items.resolve()),
        "key": str(key.resolve()), "score_script": str(scorer.resolve()),
        "n_items": len(item_data),
        "scope": "full input" if per_case is None else f"balanced pilot, {per_case} per case",
        "source_items": str(source_items.resolve()), "source_key": str(source_key.resolve()),
        "source_items_sha256": checksum(source_items), "source_key_sha256": checksum(source_key),
        "selected_ids": [item["id"] for item in item_data], "seed": seed,
        "shards": shards, "timeout_seconds": timeout_seconds,
        "items_sha256": checksum(items), "key_sha256": checksum(key),
        "scorer_sha256": checksum(scorer), "python": platform.python_version(),
        "model": MODEL, "temperature": 1.0, "top_p": 0.95,
        "reasoning": {"enabled": False}, "budgets": budgets, "variants": variants,
        "repeats": repeats,
        "prompt_sha256": checksum(ROOT / "prompts" / "extract.txt"),
    }
    write_json(out_dir / "manifest.json", manifest)
    results = {variant: {budget: [
        {"run_number": number, "status": "not_run"} for number in range(1, repeats + 1)
    ] for budget in budgets} for variant in variants}

    def persist():
        write_json(out_dir / "results.json", results)
        (out_dir / "ablation_table.md").write_text(render_report(manifest, results), encoding="utf-8")

    persist()
    stopped = False
    def run_command(command):
        try:
            return subprocess.run(command, text=True, capture_output=True,
                                  timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(command, 124, "",
                                               f"Exceeded {timeout_seconds}s subprocess timeout")

    # Budget-major ordering gets a full 1x/3x/10x curve early when possible.
    for budget in budgets:
        for variant in variants:
            for number in range(1, repeats + 1):
                if stopped:
                    continue
                stem = f"{budget}-{variant}-run{number}"
                output = out_dir / f"{stem}-answers.json"
                command = [str(ROOT / "run"), str(items.resolve()), "--budget", budget,
                           "--out", str(output)]
                if variant != "full":
                    command.extend(["--ablate", variant])
                if env_file is not None:
                    command.extend(["--env-file", str(env_file.resolve())])
                run = {"run_number": number, "status": "running", "command": command,
                       "answers": str(output)}
                results[variant][budget][number - 1] = run
                persist()
                if shards == 1:
                    execution = run_command(command)
                else:
                    groups = [[] for _ in range(min(shards, len(item_data)))]
                    for index, item in enumerate(item_data):
                        groups[index % len(groups)].append(item)

                    def run_shard(index_and_group):
                        index, group = index_and_group
                        shard_input = out_dir / f"{stem}-shard{index}-items.json"
                        shard_output = out_dir / f"{stem}-shard{index}-answers.json"
                        write_json(shard_input, group)
                        shard_command = [str(ROOT / "run"), str(shard_input), "--budget", budget,
                                         "--out", str(shard_output)]
                        if variant != "full":
                            shard_command.extend(["--ablate", variant])
                        if env_file is not None:
                            shard_command.extend(["--env-file", str(env_file.resolve())])
                        result = run_command(shard_command)
                        return result, shard_output, shard_command

                    with ThreadPoolExecutor(max_workers=len(groups)) as pool:
                        shard_runs = list(pool.map(run_shard, enumerate(groups)))
                    run["shards"] = [{"command": shard_command, "exit_code": result.returncode,
                                      "stdout": result.stdout, "stderr": result.stderr}
                                     for result, _, shard_command in shard_runs]
                    if all(result.returncode == 0 for result, _, _ in shard_runs):
                        merged = {}
                        for _, shard_output, _ in shard_runs:
                            shard_answers = json.loads(shard_output.read_text(encoding="utf-8"))
                            if not isinstance(shard_answers, dict) or set(merged) & set(shard_answers):
                                raise ValueError("Invalid or overlapping shard answers")
                            merged.update(shard_answers)
                        if set(merged) != set(key_data):
                            raise ValueError("Merged shard answers do not match input IDs")
                        write_json(output, merged)
                    execution = type("Execution", (), {
                        "returncode": max(result.returncode for result, _, _ in shard_runs),
                        "stdout": "\n".join(result.stdout for result, _, _ in shard_runs),
                        "stderr": "\n".join(result.stderr for result, _, _ in shard_runs),
                    })()
                run["pipeline_exit_code"] = execution.returncode
                run["pipeline_stdout"] = execution.stdout
                run["pipeline_stderr"] = execution.stderr
                run["model_calls"] = model_calls_from_stdout(execution.stdout)
                if execution.returncode:
                    run["status"] = "failed"
                    run["error"] = f"pipeline exited {execution.returncode}: {execution.stderr.strip()}"
                    stopped = True
                    persist()
                    continue
                scored = run_command([sys.executable, str(scorer.resolve()),
                                      str(key.resolve()), str(output), str(items.resolve())])
                run["scorer_exit_code"] = scored.returncode
                if scored.returncode:
                    run["status"] = "failed"
                    run["error"] = f"official scorer exited {scored.returncode}: {scored.stderr.strip()}"
                    stopped = True
                else:
                    try:
                        score = json.loads(scored.stdout)
                        if "macro_exact_match" not in score or "confusion" not in score:
                            raise ValueError("Official score lacks required fields")
                    except (json.JSONDecodeError, ValueError) as exc:
                        run["status"] = "failed"
                        run["error"] = f"invalid scorer output: {exc}"
                        stopped = True
                    else:
                        run["status"] = "complete"
                        run["score"] = score
                persist()
    return {"run_dir": str(out_dir), "failed": stopped,
            "completed": sum(run["status"] == "complete" for variant in variants
                             for budget in budgets for run in results[variant][budget])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", required=True, type=Path)
    parser.add_argument("--key", required=True, type=Path)
    parser.add_argument("--score-script", required=True, type=Path)
    parser.add_argument("--out-dir", type=Path,
                        default=ROOT / "experiments" / "evaluations" / uuid.uuid4().hex)
    parser.add_argument("--budgets", nargs="+", choices=list(BUDGETS), default=list(BUDGETS))
    parser.add_argument("--variants", nargs="+", choices=VARIANTS, default=list(VARIANTS))
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--per-case", type=int,
                        help="Use a seeded balanced pilot with N items of each true case")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--shards", type=int, default=1,
                        help="Parallel independent run processes; budgets remain per item")
    parser.add_argument("--timeout-seconds", type=int, default=360,
                        help="Per-shard and scorer process wall-clock timeout")
    args = parser.parse_args()
    try:
        result = evaluate(args.items, args.key, args.score_script, args.out_dir,
                          args.budgets, args.variants, args.repeats, args.env_file,
                          args.per_case, args.seed, args.shards, args.timeout_seconds)
    except (OSError, ValueError) as exc:
        print(f"Evaluation setup failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
