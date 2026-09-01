import argparse
import json
from pathlib import Path

from experiments.investigator_screen.audit import audit_fixtures
from experiments.investigator_screen.fixtures import all_fixtures
from investigator.cycle_prompt import build_investigator_cycle_prompt


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit the diagnostic one-turn Investigator screen without model calls.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit_fixtures()
    report["prompt_hashes"] = {fixture.fixture_id: __import__("hashlib").sha256(build_investigator_cycle_prompt(fixture.observation).encode()).hexdigest() for fixture in all_fixtures()}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, default=list) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, default=list))


if __name__ == "__main__":
    main()
