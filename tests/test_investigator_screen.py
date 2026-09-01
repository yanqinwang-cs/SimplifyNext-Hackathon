import pytest

from investigator.cycle import CycleError, CycleFailureCode, InvestigatorTurnResponse, InvestigatorCycleCoordinator

from experiments.investigator_screen.audit import audit_fixtures
from experiments.investigator_screen.evaluate import evaluate_payload
from experiments.investigator_screen.fixtures import all_fixtures


def _payload(*updates, next_step="local_exhausted"):
    return {"graph_updates": list(updates), "next_step": {"type": next_step, "reason": "This is a bounded, evidence-grounded local step."}}


def _proposition(identifier="P2", sources=("E1",), statement="The visible record supports this bounded proposition."):
    return {"operation": "add_proposition", "node_id": identifier, "statement": statement, "derived_from_node_ids": list(sources), "reason": "The visible evidence supports this limited proposition."}


def _relation(operation, source, target):
    return {"operation": operation, "source_node_id": source, "target_node_id": target, "reason": "The relation preserves the visible evidential meaning."}


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


def test_inv2_timing_uncertainty_may_handoff_when_no_enquiry_exists() -> None:
    fixture = all_fixtures()[1]
    payload = _payload({"operation": "add_uncertainty", "node_id": "U2", "statement": "Whether the message was before or after the session remains unresolved.", "target_node_id": "P1", "reason": "The visible date does not resolve the session boundary."})
    result = evaluate_payload(fixture, payload)
    assert result.semantic_pass and result.outcome == "PASS"


def test_inv3_accepts_direct_and_mediated_support_but_requires_support() -> None:
    fixture = all_fixtures()[2]
    direct = evaluate_payload(fixture, _payload(_relation("add_support", "E1", "P1")))
    mediated = evaluate_payload(fixture, _payload(_proposition(), _relation("add_support", "P2", "P1")))
    missing = evaluate_payload(fixture, _payload(_proposition()))
    assert direct.semantic_pass and mediated.semantic_pass
    assert "SEMANTIC_FAILURE" in missing.failure_categories


def test_inv4_accepts_mediated_conflict_but_rejects_deception_inference() -> None:
    fixture = all_fixtures()[3]
    direct = evaluate_payload(fixture, _payload(_relation("add_conflict", "E1", "P1")))
    mediated = evaluate_payload(fixture, _payload(_proposition(), _relation("add_conflict", "P2", "P1")))
    harmful = evaluate_payload(fixture, _payload(_relation("add_conflict", "E1", "P1"), {"operation": "add_hypothesis", "node_id": "H2", "statement": "The contradiction proves deliberate deception.", "reason": "The contradiction is treated as proof of intent."}))
    assert direct.semantic_pass and mediated.semantic_pass
    assert "CONTRADICTION_TO_DECEPTION" in harmful.failure_categories


def test_inv5_keeps_specificity_strict_but_accepts_broad_alternative_handoff() -> None:
    fixture = all_fixtures()[4]
    broad = {"operation": "add_hypothesis", "node_id": "H2", "statement": "Prior knowledge may explain the terminology.", "reason": "The evidence does not identify a specific source."}
    specific = {"operation": "add_hypothesis", "node_id": "H2", "statement": "A tutor directly supplied the answer.", "reason": "This is asserted as one possible source."}
    assert evaluate_payload(fixture, _payload(broad)).semantic_pass
    assert "SEMANTIC_FAILURE" in evaluate_payload(fixture, _payload(specific)).failure_categories


def test_inv7_unnecessary_clarification_remains_a_failure() -> None:
    fixture = all_fixtures()[6]
    payload = {"graph_updates": [], "next_step": {"type": "request_enquiry", "action_id": "A1", "target_uncertainty_id": "U1", "expected_information_value": "Clarification may add precision.", "reason": "The vague wording should be clarified."}}
    result = evaluate_payload(fixture, payload)
    assert not result.semantic_pass and "SEMANTIC_FAILURE" in result.failure_categories


def test_inv10_and_inv11_accept_grounded_handoff_or_review() -> None:
    inv10 = all_fixtures()[9]
    grounded = evaluate_payload(inv10, _payload(_proposition(statement="The shared revision sequence is present in E1.")))
    assert grounded.semantic_pass
    inv11 = all_fixtures()[10]
    update = {"operation": "add_uncertainty", "node_id": "U2", "statement": "Whether the sequence bears on both explanations remains open.", "target_node_id": "H1", "reason": "The visible proposition relates to both active explanations."}
    exhausted = evaluate_payload(inv11, _payload(update))
    review = evaluate_payload(inv11, {"graph_updates": [update], "next_step": {"type": "request_steward_review", "reason": "The cross-cutting local structure warrants global review."}})
    assert exhausted.semantic_pass and review.semantic_pass


def test_inv12_and_available_action_define_exhaustion_boundary() -> None:
    inv12 = all_fixtures()[11]
    assert evaluate_payload(inv12, _payload()).semantic_pass
    anti = all_fixtures()[5]
    assert not evaluate_payload(anti, _payload()).semantic_pass


def test_manual_review_flags_over_expansion_without_failing_valid_response() -> None:
    fixture = all_fixtures()[2]
    updates = [_proposition(f"P{i}") for i in range(2, 4)] + [{"operation": "add_uncertainty", "node_id": "U2", "statement": "A bounded remaining question.", "target_node_id": "P1", "reason": "The visible record leaves this question open."}, {"operation": "add_uncertainty", "node_id": "U3", "statement": "A second bounded remaining question.", "target_node_id": "P1", "reason": "The visible record leaves this question open."}, _relation("add_support", "E1", "P1")]
    result = evaluate_payload(fixture, _payload(*updates, next_step="local_exhausted"))
    assert "POSSIBLE_OVER_EXPANSION" in result.manual_review_flags
    assert result.outcome == "NEEDS_MANUAL_REVIEW"
