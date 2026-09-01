"""Write a concise offline sequential-evaluation report."""

import argparse
import json
from pathlib import Path

from experiments.steward_screen.scenarios import trajectory_scenarios
from experiments.steward_screen.sequential import summarize_trajectories


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--steps", type=int, default=4)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summarize_trajectories(trajectory_scenarios(), args.steps), indent=2) + "\n", encoding="utf-8")
    print(args.output)
