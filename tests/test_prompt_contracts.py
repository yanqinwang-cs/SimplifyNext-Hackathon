import json

import pytest
from pydantic import TypeAdapter

from experiments.stage2_request_driven.runner import call_and_validate_steward, graph
from experiments.steward_screen.models import StewardScenario, ExpectedState
from experiments.steward_screen.prompt import build_prompt as build_steward_prompt
from investigator.cycle import InvestigatorObservation
from investigator.cycle_prompt import build_investigator_cycle_prompt
from investigator.roles import InvestigationFocus, StewardDecision
from investigator.llm import ModelCallMetadata, MockModelClient, ModelParseError


def investigator_prompt() -> str:
    observation = InvestigatorObservation(
        current_focus=InvestigationFocus(node_id="U1"),
        local_graph=graph(),
        tenure_turn_count=0,
        max_turns_per_tenure=4,
        turns_remaining=4,
    )
    return build_investigator_cycle_prompt(observation)


def test_investigator_prompt_uses_policy_and_runtime_schema_without_blanket_evidence_ban() -> None:
    prompt = investigator_prompt()
    assert "<OPERATION_POLICY>" in prompt
    assert "IF" in prompt and "ELIF" in prompt and "NEVER" in prompt
    assert "Do not create evidence" not in prompt
    assert json.dumps("request_evidence") in prompt
    assert json.dumps("InvestigatorTurnResponse") not in prompt or "graph_updates" in prompt
    assert "properties" in prompt


def test_investigator_prompt_distinguishes_sources_from_graph_locality() -> None:
    prompt = investigator_prompt()
    assert "Raw SourceRegistry visibility is a separate namespace" in prompt
    assert "active_reasoning_node_ids" in prompt
    assert "visible raw source is not inaccessible" in prompt


def test_steward_prompt_uses_schema_operations_and_forbids_release_authority() -> None:
    scenario = StewardScenario(scenario_id="S", description="A bounded case-management review.", graph=graph(), focus=InvestigationFocus(node_id="U1"), expected_operation="keep_focus", expected_state=ExpectedState(focus_node_id="U1"))
    prompt = build_steward_prompt(scenario)
    assert "<OPERATION_POLICY>" in prompt
    assert "KEEP_FOCUS" in prompt and "STOP_UNRESOLVED" in prompt
    assert "create or release raw sources" in prompt
    with pytest.raises(Exception):
        TypeAdapter(StewardDecision).validate_python({"operation": "release", "assessment": "x", "reason": "x"})


def test_steward_validation_failure_preserves_raw_and_provider_json() -> None:
    client = MockModelClient({"operation": "release", "assessment": "x", "reason": "x"}, ModelCallMetadata(provider="mock", model="mock", latency_seconds=0.01, parse_success=True))
    trace: dict[str, object] = {}
    with pytest.raises(ModelParseError):
        call_and_validate_steward(client, "prompt", trace)
    assert trace["raw_output"] == client.response
    assert trace["provider_json"] == client.response
    assert trace["attempts"] == 2
    assert "release" in str(trace["validation_error"])
