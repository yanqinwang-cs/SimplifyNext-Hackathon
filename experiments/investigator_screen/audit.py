import hashlib
import json
from collections import Counter

from experiments.investigator_screen.fixtures import all_fixtures
from investigator.cycle_prompt import build_investigator_cycle_prompt


def observation_fingerprint(fixture) -> str:
    payload = json.dumps(fixture.observation.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def _basis_resolves(fixture, declaration: str) -> bool:
    kind, reference = declaration.split(":", 1)
    observation = fixture.observation
    if kind == "NODE_STATEMENT":
        node_id, fragment = reference.split(":", 1)
        node = observation.local_graph.nodes.get(node_id)
        return node is not None and fragment in node.statement
    if kind == "GRAPH_EDGE":
        source, relation, target = reference.split("|")
        return any(edge.source_id == source and edge.relation.value == relation and edge.target_id == target for edge in observation.local_graph.edges.values())
    if kind == "AVAILABLE_ENQUIRY":
        action, enquiry_kind, target, fragment = reference.split(":", 3)
        return any(item.action_id == action and item.kind.value == enquiry_kind and target in item.addressable_uncertainty_ids and fragment.lower() in item.description.lower() for item in observation.available_enquiries)
    if kind == "CURRENT_FOCUS":
        return observation.current_focus.node_id == reference
    if kind == "CYCLE_STATE":
        field, value = reference.split("|", 1)
        return str(getattr(observation, field)) == value
    raise AssertionError(f"unknown public basis kind: {kind}")


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
        for requirement in [*fixture.required, *fixture.forbidden]:
            if not requirement.basis or not all(_basis_resolves(fixture, declaration) for declaration in requirement.basis):
                raise AssertionError(f"missing public basis for {fixture.fixture_id}:{requirement.kind}")
    return {"fixtures": len(fixtures), "fingerprints": fingerprints, "next_step_coverage": Counter(step for item in fixtures for step in item.acceptable_next_steps)}
