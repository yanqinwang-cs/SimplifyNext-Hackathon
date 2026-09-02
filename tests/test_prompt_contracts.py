import json

import pytest
from pydantic import TypeAdapter

from experiments.stage2_request_driven.runner import call_and_validate_steward, graph
from experiments.steward_screen.models import StewardScenario, ExpectedState
from experiments.steward_screen.prompt import build_prompt as build_steward_prompt
from investigator.cycle import InvestigatorObservation
from investigator.cycle_prompt import build_investigator_cycle_prompt
from investigator.roles import InvestigationFocus, ProductionStewardDecision, StewardDecision, render_production_steward_contract
from investigator.llm import ModelCallMetadata, MockModelClient, ModelParseError
from investigator.services.evidence_requests import HumanEvidenceWorkflow
from investigator.services.production_runner import ProductionInvestigationRunner, StewardEnvelope
from investigator.state.repository import CaseRepository


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


def test_production_steward_contract_exposes_exact_discriminator_and_operations() -> None:
    contract = render_production_steward_contract()
    assert 'TOP-LEVEL DISCRIMINATOR FIELD: exactly "operation"' in contract
    assert 'DO NOT USE "decision"' in contract
    for operation in ("keep_focus", "shift_focus", "generalize", "archive", "reactivate", "stop_unresolved", "request_information"):
        assert f'"{operation}"' in contract
    assert '"operation":"stop_unresolved"' in contract
    assert all(field in contract for field in ("assessment", "reason", "important_unresolved_ids", "reopening_conditions"))


def test_production_steward_rejects_decision_discriminator_and_accepts_stop() -> None:
    adapter = TypeAdapter(ProductionStewardDecision)
    with pytest.raises(Exception):
        adapter.validate_python({"decision": "stop_unresolved", "assessment": "x", "reason": "x", "important_unresolved_ids": ["U1"], "reopening_conditions": "new evidence"})
    parsed = adapter.validate_python({"operation": "stop_unresolved", "assessment": "x", "reason": "x", "important_unresolved_ids": ["U1"], "reopening_conditions": "new evidence"})
    assert parsed.operation == "stop_unresolved"


def test_steward_correction_prompt_repeats_exact_contract_for_wrong_discriminator(tmp_path) -> None:
    payload = {"decision": "stop_unresolved", "assessment": "x", "reason": "x", "important_unresolved_ids": ["U1"], "reopening_conditions": "new evidence"}
    client = MockModelClient(payload, ModelCallMetadata(provider="mock", model="test", latency_seconds=0, parse_success=True))
    runner = ProductionInvestigationRunner(HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases")), client)
    with pytest.raises(RuntimeError):
        runner._call_with_retry("initial prompt", StewardEnvelope, lambda value: TypeAdapter(ProductionStewardDecision).validate_python(value.root), {}, "Steward", contract=render_production_steward_contract())
    correction_prompt = client.calls[1][0]
    assert 'exactly "operation"' in correction_prompt
    assert '"stop_unresolved"' in correction_prompt
    assert 'DO NOT USE "decision"' in correction_prompt


def test_investigator_prompt_states_both_discriminators() -> None:
    prompt = investigator_prompt()
    assert 'Graph updates use the exact field "operation"' in prompt
    assert 'next-step object uses the exact field "type"' in prompt


def test_first_investigator_prompt_has_empty_recent_action_memory_and_anti_repetition_policy() -> None:
    prompt = investigator_prompt()
    assert "<RECENT_INVESTIGATOR_ACTIONS>\nNone." in prompt
    assert "Do not recreate an existing observation, proposition, hypothesis, or uncertainty merely by paraphrasing it." in prompt
    assert "not progress" in prompt
    assert "concrete, case-relevant, answerable, materially useful question" in prompt


def test_steward_validation_failure_preserves_raw_and_provider_json() -> None:
    client = MockModelClient({"operation": "release", "assessment": "x", "reason": "x"}, ModelCallMetadata(provider="mock", model="mock", latency_seconds=0.01, parse_success=True))
    trace: dict[str, object] = {}
    with pytest.raises(ModelParseError):
        call_and_validate_steward(client, "prompt", trace)
    assert trace["raw_output"] == client.response
    assert trace["provider_json"] == client.response
    assert trace["attempts"] == 2
    assert "release" in str(trace["validation_error"])
