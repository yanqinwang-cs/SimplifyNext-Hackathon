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
        public = {fixture.fixture_id: fixture.observation.model_dump(mode="json") for fixture in all_fixtures()}
        args.output.with_name("public_observations.json").write_text(json.dumps(public, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        hidden = {fixture.fixture_id: {"description": fixture.description, "required": [item.__dict__ for item in fixture.required], "forbidden": [item.__dict__ for item in fixture.forbidden], "acceptable_next_steps": sorted(fixture.acceptable_next_steps), "public_basis": fixture.public_basis} for fixture in all_fixtures()}
        args.output.with_name("hidden_evaluator.json").write_text(json.dumps(hidden, indent=2, default=list, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, default=list))


if __name__ == "__main__":
    main()
