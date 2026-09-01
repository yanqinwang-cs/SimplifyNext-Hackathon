import pytest
from pydantic import ValidationError

from investigator.cycle import (
    AvailableEnquiry, CycleError, CycleFailureCode, CycleStatus, EnquiryCompletion,
    EnquiryKind, InvestigatorCycleCoordinator, InvestigatorTurnResponse,
    RequestEnquiry,
)
from investigator.cycle_prompt import build_investigator_cycle_prompt
from investigator.graph import CaseGraph, EdgeRelation, GraphEdge, GraphNode, GraphNodeType
from investigator.roles import HandoffToHumanDecision, InvestigationFocus, StewardReviewContext


def graph() -> CaseGraph:
    nodes = {identifier: GraphNode(id=identifier, node_type=kind, statement=identifier) for identifier, kind in {
        "E1": GraphNodeType.EVIDENCE, "P1": GraphNodeType.PROPOSITION, "H1": GraphNodeType.HYPOTHESIS,
        "H2": GraphNodeType.HYPOTHESIS, "U1": GraphNodeType.UNCERTAINTY,
    }.items()}
    edges = {
        "E1_SUPPORTS_P1": GraphEdge(id="E1_SUPPORTS_P1", source_id="E1", target_id="P1", relation=EdgeRelation.SUPPORTS),
        "H1_DEPENDS_ON_P1": GraphEdge(id="H1_DEPENDS_ON_P1", source_id="H1", target_id="P1", relation=EdgeRelation.DEPENDS_ON),
        "U1_TARGETS_H1": GraphEdge(id="U1_TARGETS_H1", source_id="U1", target_id="H1", relation=EdgeRelation.TARGETS),
    }
    return CaseGraph(case_id="cycle", nodes=nodes, edges=edges)


def enquiry(action_id="A3", target_ids=None):
    return AvailableEnquiry(action_id=action_id, kind=EnquiryKind.VERIFY, description="Verify a bounded fact.", addressable_uncertainty_ids=target_ids or ["U1"])


def coordinator(**kwargs):
    return InvestigatorCycleCoordinator(graph(), InvestigationFocus(node_id="H1"), available_enquiries=[enquiry()], **kwargs)


def update(node_id="P3"):
    return {"operation": "add_proposition", "node_id": node_id, "statement": "local observation", "derived_from_node_ids": ["P1"], "reason": "It is useful locally."}


def test_turn_schema_has_four_next_steps_and_enforces_noop_and_cap():
    values = ("continue_local", "request_enquiry", "request_steward_review", "local_exhausted")
    for value in values:
        assert value in str(InvestigatorTurnResponse.model_json_schema())
    with pytest.raises(ValidationError):
        InvestigatorTurnResponse.model_validate({"graph_updates": [], "next_step": {"type": "continue_local", "reason": "again"}})
    with pytest.raises(CycleError) as error:
        coordinator().apply_turn({"graph_updates": [update(f"P{i}") for i in range(3, 9)], "next_step": {"type": "local_exhausted", "reason": "done"}})
    assert error.value.code is CycleFailureCode.TOO_MANY_GRAPH_UPDATES
    assert coordinator().apply_turn({"graph_updates": [], "next_step": {"type": "local_exhausted", "reason": "No useful local step remains."}}).status is CycleStatus.AWAITING_STEWARD


def test_turn_is_atomic_and_allows_ordered_new_objects():
    item = InvestigatorCycleCoordinator(graph(), InvestigationFocus(node_id="H1"), available_enquiries=[enquiry(target_ids=["U3"])])
    response = {"graph_updates": [update(), {"operation": "add_uncertainty", "node_id": "U3", "statement": "question", "target_node_id": "P3", "reason": "It matters."}], "next_step": {"type": "request_enquiry", "action_id": "A3", "target_uncertainty_id": "U3", "expected_information_value": "It can reduce the question.", "reason": "The listed check is useful."}}
    state = item.apply_turn(response)
    assert state.status is CycleStatus.ENQUIRY_IN_FLIGHT and "U3_TARGETS_P3" in item.graph.edges
    before = set(item.graph.nodes)
    with pytest.raises(CycleError):
        item.apply_turn({"graph_updates": [update("P4"), {"operation": "add_uncertainty", "node_id": "U4", "statement": "bad", "target_node_id": "P999", "reason": "bad"}], "next_step": {"type": "local_exhausted", "reason": "bad"}})
    assert set(item.graph.nodes) == before


def test_enquiry_availability_completion_and_budget():
    item = coordinator(max_turns_per_tenure=1)
    state = item.apply_turn({"graph_updates": [], "next_step": {"type": "request_enquiry", "action_id": "A3", "target_uncertainty_id": "U1", "expected_information_value": "It can reduce uncertainty.", "reason": "The check is useful now."}})
    assert state.status is CycleStatus.ENQUIRY_IN_FLIGHT and state.steward_review_required_after_enquiry
    with pytest.raises(CycleError) as error:
        item.apply_turn({"graph_updates": [], "next_step": {"type": "local_exhausted", "reason": "wait"}})
    assert error.value.code is CycleFailureCode.ENQUIRY_ALREADY_IN_FLIGHT
    item.complete_enquiry({"action_id": "A3", "released_evidence_ids": []})
    assert item.cycle.status is CycleStatus.AWAITING_STEWARD
    with pytest.raises(CycleError):
        item.complete_enquiry({"action_id": "A3", "released_evidence_ids": []})


def test_snapshot_isolated_revision_and_steward_handoff():
    item = coordinator()
    item.apply_turn({"graph_updates": [], "next_step": {"type": "request_steward_review", "reason": "Global reassessment is warranted."}})
    snapshot = item.steward_snapshot()
    snapshot.graph.add_node(GraphNode(id="P9", node_type=GraphNodeType.PROPOSITION, statement="snapshot only"))
    assert "P9" not in item.graph.nodes
    with pytest.raises(CycleError) as error:
        item.apply_steward_decision({"operation": "keep_focus", "assessment": "keep", "reason": "The focus remains useful."}, snapshot.case_revision - 1)
    assert error.value.code is CycleFailureCode.STALE_STEWARD_REVISION
    item.apply_steward_decision({"operation": "keep_focus", "assessment": "keep", "reason": "The focus remains useful."}, snapshot.case_revision)
    assert item.cycle.status is CycleStatus.LOCAL_ACTIVE and item.cycle.tenure_turn_count == 0


def test_stop_requires_trusted_context_and_prompt_exposes_local_schema():
    item = coordinator(review_context=StewardReviewContext(global_frontier_assessed=True, local_frontier_exhausted=True, active_unresolved_ids=["U1"]))
    item.apply_turn({"graph_updates": [], "next_step": {"type": "local_exhausted", "reason": "No useful local step remains."}})
    item.apply_steward_decision({"operation": "handoff_to_human", "assessment": "No frontier remains", "reason": "Trusted review supports handoff.", "important_unresolved_ids": ["U1"], "reopening_conditions": "New evidence.", "handoff_summary": "Return the unresolved case to a human."}, item.cycle.case_revision)
    assert item.cycle.status is CycleStatus.STOPPED and item.cycle.termination_reason == "HANDOFF_TO_HUMAN"
    prompt = build_investigator_cycle_prompt(coordinator().observation())
    assert "InvestigatorTurnResponse" in prompt and "add_proposition" in prompt and "local_exhausted" in prompt
    assert '"local_graph"' in prompt and '"available_enquiries"' in prompt


def ready_context(**overrides):
    values = {"global_frontier_assessed": True, "local_frontier_exhausted": True, "available_action_ids": ["A1"], "materially_usable_action_ids": [], "active_unresolved_ids": ["U1"], "obvious_useful_region_remains": False}
    values.update(overrides)
    return StewardReviewContext(**values)


def _awaiting_steward(**context_overrides):
    item = coordinator(review_context=ready_context(**context_overrides))
    item.apply_turn({"graph_updates": [], "next_step": {"type": "local_exhausted", "reason": "The local frontier is complete."}})
    return item


def test_handoff_to_human_requires_trusted_exhaustion_and_terminates():
    ready = {"operation": "handoff_to_human", "assessment": "handoff", "reason": "No consequential investigative uncertainty requires another enquiry.", "important_unresolved_ids": [], "reopening_conditions": "Reopen for materially contradictory evidence.", "handoff_summary": "The exhausted case is ready for human review."}
    item = _awaiting_steward()
    state = item.apply_steward_decision(ready, item.cycle.case_revision)
    assert state.status is CycleStatus.STOPPED and state.termination_reason == "HANDOFF_TO_HUMAN"
    for overrides in ({"global_frontier_assessed": False}, {"materially_usable_action_ids": ["A1"]}, {"obvious_useful_region_remains": True}):
        item = _awaiting_steward(**overrides)
        with pytest.raises(CycleError):
            item.apply_steward_decision(ready, item.cycle.case_revision)


def test_handoff_preserves_consequential_uncertainty_and_rejects_in_flight_enquiry():
    item = _awaiting_steward()
    decision = {"operation": "handoff_to_human", "assessment": "handoff", "reason": "A consequential question remains listed.", "important_unresolved_ids": ["U1"], "reopening_conditions": "New evidence.", "handoff_summary": "This should not pass."}
    state = item.apply_steward_decision(decision, item.cycle.case_revision)
    assert state.termination_reason == "HANDOFF_TO_HUMAN"
    item = coordinator()
    item.apply_turn({"graph_updates": [], "next_step": {"type": "request_enquiry", "action_id": "A3", "target_uncertainty_id": "U1", "expected_information_value": "The check can change the case.", "reason": "The listed check is useful."}})
    with pytest.raises(CycleError) as error:
        item.apply_steward_decision({"operation": "handoff_to_human", "assessment": "handoff", "reason": "No further work.", "important_unresolved_ids": [], "reopening_conditions": "New evidence.", "handoff_summary": "Ready."}, item.cycle.case_revision, review_context=ready_context())
    assert error.value.code is CycleFailureCode.STEWARD_WRITE_DURING_IN_FLIGHT
