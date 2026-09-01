import pytest

from investigator.cycle import CycleError, CycleFailureCode, InvestigatorTurnResponse, InvestigatorCycleCoordinator

from experiments.investigator_screen.audit import audit_fixtures
from experiments.investigator_screen.evaluate import evaluate_payload
from experiments.investigator_screen.fixtures import all_fixtures


def test_exactly_twelve_public_fixtures_and_audit_passes() -> None:
    fixtures = all_fixtures()
    assert [item.fixture_id for item in fixtures] == [f"INV{i}" for i in range(1, 13)]
    assert audit_fixtures()["fixtures"] == 12


def test_fixture_requirements_have_public_basis_and_prompt_excludes_evaluator_metadata() -> None:
    for fixture in all_fixtures():
        assert all(requirement.basis for requirement in fixture.required)
        assert "acceptable_next_steps" not in str(fixture.observation)


def test_production_turn_contract_rejects_continue_without_graph_work() -> None:
    try:
        InvestigatorTurnResponse.model_validate({"graph_updates": [], "next_step": {"type": "continue_local", "reason": "No work"}})
    except Exception:
        pass
    else:
        raise AssertionError("continue_local without graph updates must fail")


def test_request_enquiry_uses_production_coordinator_and_semantic_evaluator() -> None:
    fixture = all_fixtures()[5]
    payload = {"graph_updates": [], "next_step": {"type": "request_enquiry", "action_id": "A1", "target_uncertainty_id": "U1", "expected_information_value": "It narrows timing.", "reason": "The listed clarification is useful."}}
    result = evaluate_payload(fixture, payload)
    assert result.schema_valid and result.production_applied and result.semantic_pass


def test_production_coordinator_rejects_more_than_five_updates() -> None:
    fixture = all_fixtures()[9]
    updates = [{"operation": "add_proposition", "node_id": f"P{i}", "statement": "local observation", "derived_from_node_ids": ["E1"], "reason": "The visible record supports this local observation."} for i in range(2, 8)]
    with pytest.raises(CycleError) as error:
        InvestigatorCycleCoordinator(fixture.observation.local_graph, fixture.observation.current_focus).apply_turn({"graph_updates": updates, "next_step": {"type": "local_exhausted", "reason": "No further local work remains."}})
    assert error.value.code is CycleFailureCode.TOO_MANY_GRAPH_UPDATES


def test_failed_graph_update_is_atomic() -> None:
    fixture = all_fixtures()[1]
    coordinator = InvestigatorCycleCoordinator(fixture.observation.local_graph, fixture.observation.current_focus)
    before = set(coordinator.graph.nodes)
    with pytest.raises(CycleError) as error:
        coordinator.apply_turn({"graph_updates": [{"operation": "add_uncertainty", "node_id": "U2", "statement": "question", "target_node_id": "P999", "reason": "This invalid target is only a test."}], "next_step": {"type": "local_exhausted", "reason": "No further local work remains."}})
    assert error.value.code is CycleFailureCode.TURN_ATOMIC_ROLLBACK
    assert set(coordinator.graph.nodes) == before
