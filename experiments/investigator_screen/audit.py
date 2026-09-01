import hashlib
from collections import Counter

from experiments.investigator_screen.fixtures import all_fixtures
from investigator.cycle_prompt import build_investigator_cycle_prompt


def observation_fingerprint(fixture) -> str:
    observation = fixture.observation.model_dump(mode="json")
    return hashlib.sha256(str(observation).encode()).hexdigest()


def audit_fixtures() -> dict:
    fixtures = all_fixtures()
    fingerprints = [observation_fingerprint(item) for item in fixtures]
    if len(set(fixtures[i].fixture_id for i in range(len(fixtures)))) != len(fixtures):
        raise AssertionError("fixture IDs must be unique")
    if len(set(fingerprints)) != len(fingerprints):
        raise AssertionError("public observations must be distinct")
    for fixture in fixtures:
        prompt = build_investigator_cycle_prompt(fixture.observation)
        if "acceptable_next_steps" in prompt or "public_basis" in prompt or "protected_node_ids" in prompt:
            raise AssertionError(f"answer metadata leaked into prompt for {fixture.fixture_id}")
        for requirement in fixture.required:
            if not requirement.basis:
                raise AssertionError(f"missing public basis for {fixture.fixture_id}:{requirement.kind}")
    return {"fixtures": len(fixtures), "fingerprints": fingerprints, "next_step_coverage": Counter(step for item in fixtures for step in item.acceptable_next_steps)}
