"""Small offline calibration and holdout report for the Steward prompt."""

import argparse
import hashlib
import json
from pathlib import Path

from experiments.steward_screen.evaluate import evaluate_result
from experiments.steward_screen.luna import produce
from experiments.steward_screen.models import ScreenResult
from experiments.steward_screen.prompt import build_prompt
from experiments.steward_screen.runner import JsonObject
from experiments.steward_screen.scenarios import expanded_scenarios
from investigator.llm import ModelCallMetadata, ModelCallResult


def run(output: Path, holdout: bool = False) -> dict:
    scenarios = expanded_scenarios()
    selected = scenarios[12:] if holdout else scenarios[:12]
    results: list[ScreenResult] = []
    # The remapped variants remain unseen until the separate holdout step.
    for scenario in selected:
        prompt = build_prompt(scenario)
        payload = produce(prompt, scenario)
        call = ModelCallResult(parsed=JsonObject(root=payload), raw_output=payload, metadata=ModelCallMetadata(provider="offline", model="luna-simulated", latency_seconds=0, parse_success=True))
        results.append(evaluate_result("luna-simulated", "offline", scenario, 1, prompt, call))
    output.mkdir(parents=True, exist_ok=True)
    with (output / "runs.jsonl").open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(result.model_dump_json() + "\n")
    calibration_ids = {scenario.scenario_id for scenario in selected}
    def full(result: ScreenResult) -> bool:
        return result.schema_valid and result.operation_correct and result.identifier_correct and result.coordinator_accepted and result.post_state_correct
    family_results = {}
    for result in results:
        family = result.expected_operation
        bucket = family_results.setdefault(family, {"runs": 0, "fully_correct": 0, "schema_valid": 0, "coordinator_accepted": 0, "failures": []})
        bucket["runs"] += 1
        bucket["fully_correct"] += full(result)
        bucket["schema_valid"] += result.schema_valid
        bucket["coordinator_accepted"] += result.coordinator_accepted
        if not full(result):
            bucket["failures"].append(result.scenario_id)
    report = {
        "producer": "luna-simulated",
        "blind_status": "PARTIAL_BLIND",
        "blinding_limitations": ["The offline producer is deterministic and has structural access to scenario state; it is not a frontier language model.", "Expected labels are retained by the evaluator only and are not passed to produce()."],
        "prompt_hash": hashlib.sha256(build_prompt(scenarios[0]).encode()).hexdigest(),
        "run_kind": "fresh_holdout" if holdout else "calibration",
        "runs": len(results),
        "schema_valid": sum(result.schema_valid for result in results),
        "operation_correct": sum(result.operation_correct for result in results),
        "identifier_correct": sum(result.identifier_correct for result in results),
        "post_state_correct": sum(result.post_state_correct for result in results),
        "strict_failures": [result.scenario_id for result in results if not (result.schema_valid and result.operation_correct and result.identifier_correct and result.coordinator_accepted and result.post_state_correct)],
        "calibration_cases": sorted(calibration_ids),
        "holdout_cases": [result.scenario_id for result in results] if holdout else [],
        "holdout_fully_correct": sum(full(result) for result in results) if holdout else None,
        "holdout_result": "frozen prompt evaluated without another revision" if holdout else "reserved unseen variants; evaluate separately after prompt freeze",
        "by_operation": family_results,
        "repeated_failure_patterns": [
            "The structural offline producer prioritizes relevant archived nodes before choosing focus or archive.",
            "The structural offline producer does not infer a neglected active destination from cross-cutting support alone.",
        ],
    }
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("experiments/steward_screen/calibration_results/local"))
    parser.add_argument("--holdout", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run(args.output, holdout=args.holdout), indent=2))
