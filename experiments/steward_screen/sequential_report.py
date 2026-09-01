"""Write a concise offline sequential-evaluation report."""

import argparse
import json
from pathlib import Path

from experiments.steward_screen.scenarios import trajectory_scenarios
from experiments.steward_screen.sequential import multiple_valid_cases, summarize_trajectories


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--steps", type=int, default=4)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = summarize_trajectories(trajectory_scenarios(), args.steps)
    report["multiple_valid"] = [{"scenario_id": case.scenario_id, "acceptable_first_operations": sorted(case.acceptable_operations), "producer_operation": report["details"][0]["operations"][0], "accepted": report["details"][0]["operations"][0] in case.acceptable_operations} for case in multiple_valid_cases()]
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(args.output)
