import pytest

from investigator.cycle import CycleError, CycleStatus, InvestigatorCycleCoordinator
from investigator.graph import CaseGraph, GraphNode, GraphNodeType
from investigator.models.source import SourceType
from investigator.roles.focus import InvestigationFocus
from investigator.roles.steward import StewardRequestOpenDecision
from investigator.sources import SourceRegistry
from investigator.state.case_state import CaseState


def graph() -> CaseGraph:
    return CaseGraph(case_id="case-01", nodes={"U1": GraphNode(id="U1", node_type=GraphNodeType.UNCERTAINTY, statement="Open question")}, edges={})


def test_local_ref_allocates_system_id_and_resolves_same_turn_relation():
    from investigator.roles.coordinator import GraphInvestigationCoordinator
    coordinator = GraphInvestigationCoordinator(graph(), InvestigationFocus(node_id="U1"))
    coordinator.apply_investigator_update({"operation": "add_evidence", "local_ref": "observation", "statement": "A readable record says X.", "source_ids": ["S1"], "reason": "Direct observation."})
    coordinator.apply_investigator_update({"operation": "add_uncertainty", "local_ref": "follow_up", "statement": "Whether X is complete.", "target_node_id": "observation", "reason": "The observation leaves a question."})
    assert "observation" not in coordinator.graph.nodes
    assert any(node.semantic_key == "observation" and node.id.startswith("node_") for node in coordinator.graph.nodes.values())


def test_standalone_nodes_allocate_canonical_ids_without_model_identity():
    from investigator.roles.coordinator import GraphInvestigationCoordinator
    source_graph = graph()
    source_graph.add_node(GraphNode(id="E1", node_type=GraphNodeType.EVIDENCE, statement="Existing observation"))
    source_graph.add_node(GraphNode(id="H1", node_type=GraphNodeType.HYPOTHESIS, statement="Existing explanation"))
    coordinator = GraphInvestigationCoordinator(source_graph, InvestigationFocus(node_id="U1"), full_graph_visibility=True)
    coordinator.apply_investigator_update({"operation": "add_evidence", "statement": "A direct observation.", "source_ids": ["S1"], "reason": "Record it."})
    coordinator.apply_investigator_update({"operation": "add_proposition", "statement": "A bounded inference.", "derived_from_node_ids": ["E1"], "reason": "Derive it."})
    coordinator.apply_investigator_update({"operation": "add_hypothesis", "statement": "A broad explanation.", "reason": "Keep alternatives explicit."})
    coordinator.apply_investigator_update({"operation": "add_uncertainty", "statement": "What remains unresolved?", "target_node_id": "H1", "reason": "Record the open question."})
    assert sum(node.id.startswith("node_") for node in coordinator.graph.nodes.values()) == 4


def test_request_information_is_optional_target_and_value():
    coordinator = InvestigatorCycleCoordinator(graph(), InvestigationFocus(node_id="U1"))
    state = coordinator.apply_turn({"graph_updates": [], "next_step": {"type": "request_information", "question": "What records clarify the assessment timeline?"}})
    assert state.evidence_request is not None
    assert state.evidence_request.information_sought == "What records clarify the assessment timeline?"
    assert state.evidence_request.target_uncertainty_id is None
    assert state.evidence_request.expected_information_value is None


def test_request_information_target_is_validated_when_supplied():
    coordinator = InvestigatorCycleCoordinator(graph(), InvestigationFocus(node_id="U1"))
    with pytest.raises(CycleError, match="active local uncertainty"):
        coordinator.apply_turn({"graph_updates": [], "next_step": {"type": "request_information", "question": "What records clarify the timeline?", "target_uncertainty_id": "U999"}})


def test_source_is_read_only_and_has_no_semantic_edges():
    state = CaseState(case_id="case-01", title="Test")
    state.reasoning_graph = graph()
    source = SourceRegistry.register_raw_source(state, "record.txt", "readable body", {})
    assert state.reasoning_graph.nodes[source.id].node_type is GraphNodeType.SOURCE
    assert not state.reasoning_graph.edges
    assert source.source_type is SourceType.OTHER


def test_steward_can_create_bounded_human_request_without_graph_mutation():
    coordinator = InvestigatorCycleCoordinator(graph(), InvestigationFocus(node_id="U1"))
    coordinator.apply_turn({"graph_updates": [], "next_step": {"type": "local_exhausted", "reason": "No useful local step remains."}})
    before = coordinator.graph.model_dump(mode="json")
    coordinator.apply_steward_decision(StewardRequestOpenDecision(operation="request_open", assessment="Context is needed", reason="The current record does not resolve timing.", information_sought="Relevant timing context for the assessment period", expected_information_value="It could distinguish competing explanations."), 1)
    assert coordinator.cycle.status is CycleStatus.WAITING_FOR_EVIDENCE
    assert coordinator.graph.model_dump(mode="json") == before
    assert coordinator.cycle.evidence_request.target_uncertainty_id is None


def test_steward_can_create_common_request_information():
    coordinator = InvestigatorCycleCoordinator(graph(), InvestigationFocus(node_id="U1"))
    coordinator.apply_turn({"graph_updates": [], "next_step": {"type": "local_exhausted", "reason": "No useful local work remains."}})
    coordinator.apply_steward_decision({"operation": "request_information", "assessment": "Context is needed", "question": "What timing records clarify the assessment?"}, 1)
    assert coordinator.cycle.evidence_request.information_sought == "What timing records clarify the assessment?"
    assert coordinator.cycle.evidence_request.expected_information_value is None


def test_open_request_rejects_vacuous_text():
    with pytest.raises(ValueError):
        InvestigatorCycleCoordinator(graph(), InvestigationFocus(node_id="U1")).apply_turn({"graph_updates": [], "next_step": {"type": "request_open", "reason": "Need context", "information_sought": "more information", "expected_information_value": "Could help."}})
