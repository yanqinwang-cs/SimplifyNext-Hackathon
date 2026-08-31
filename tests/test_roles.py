import pytest

from investigator.graph import CaseGraph, EdgeRelation, GraphEdge, GraphNode, GraphNodeType, GraphStatus
from investigator.roles import GraphInvestigationCoordinator, InvestigationFocus, InvestigatorOperation, InvestigatorUpdate, StewardDecision, StewardOperation
from investigator.steward.features import neglected_regions, region_health, tunnel_vision_indicators


def scenario_graph() -> CaseGraph:
    nodes = {
        "E1": GraphNode(id="E1", node_type=GraphNodeType.EVIDENCE, statement="Direct evidence", metadata={"source_id": "s1"}),
        "E2": GraphNode(id="E2", node_type=GraphNodeType.EVIDENCE, statement="Weak evidence", metadata={"source_id": "s2"}),
        "P1": GraphNode(id="P1", node_type=GraphNodeType.PROPOSITION, statement="The recorded mark is accurate."),
        "P2": GraphNode(id="P2", node_type=GraphNodeType.PROPOSITION, statement="A scoring error affected the result."),
        "H1": GraphNode(id="H1", node_type=GraphNodeType.HYPOTHESIS, statement="Assistance contributed."),
        "H1.1": GraphNode(id="H1.1", node_type=GraphNodeType.HYPOTHESIS, statement="Communication assistance occurred."),
        "H1.1.1": GraphNode(id="H1.1.1", node_type=GraphNodeType.HYPOTHESIS, statement="A concealed mechanism was used."),
        "H2": GraphNode(id="H2", node_type=GraphNodeType.HYPOTHESIS, statement="Performance has a permitted explanation."),
        "U1": GraphNode(id="U1", node_type=GraphNodeType.UNCERTAINTY, statement="Is the score valid?"),
    }
    def edge(source, relation, target, strength=None):
        return GraphEdge(id=f"{source}_{relation.value.upper()}_{target}", source_id=source, target_id=target, relation=relation, strength=strength)
    edges = {
        "E1_SUPPORTS_H1.1": edge("E1", EdgeRelation.SUPPORTS, "H1.1"),
        "E2_SUPPORTS_H1.1.1": edge("E2", EdgeRelation.SUPPORTS, "H1.1.1"),
        "H1.1_SPECIALIZES_H1": edge("H1.1", EdgeRelation.SPECIALIZES, "H1"),
        "H1.1.1_SPECIALIZES_H1.1": edge("H1.1.1", EdgeRelation.SPECIALIZES, "H1.1"),
        "H1_DEPENDS_ON_P1": edge("H1", EdgeRelation.DEPENDS_ON, "P1"),
        "H2_DEPENDS_ON_P1": edge("H2", EdgeRelation.DEPENDS_ON, "P1"),
        "P2_CONFLICTS_P1": edge("P2", EdgeRelation.CONFLICTS, "P1"),
        "U1_TARGETS_P1": edge("U1", EdgeRelation.TARGETS, "P1"),
    }
    return CaseGraph(case_id="synthetic", nodes=nodes, edges=edges)


def test_sequential_investigator_steward_flow_shifts_to_cross_cutting_region() -> None:
    graph = scenario_graph()
    focus = InvestigationFocus(node_id="H1")
    coordinator = GraphInvestigationCoordinator(graph, focus)
    coordinator.apply_investigator_update(InvestigatorUpdate(operation=InvestigatorOperation.MOVE_FOCUS, focus_node_id="H1.1"))
    coordinator.apply_investigator_update(InvestigatorUpdate(operation=InvestigatorOperation.MOVE_FOCUS, focus_node_id="H1.1.1"))
    indicators = tunnel_vision_indicators(graph, coordinator.focus)
    assert indicators.same_root_steps == 2
    assert indicators.specialization_depth_change == 2
    assert "P1" in indicators.neglected_candidate_ids
    assert region_health(graph, coordinator.focus).shared_dependency_count == 1
    before = graph.model_dump(mode="json")
    coordinator.review_with_steward(StewardDecision(assessment="P1 is cross-cutting.", operation=StewardOperation.SHIFT_FOCUS, destination_node_id="P1", reason="It affects both roots."))
    assert coordinator.focus.node_id == "P1"
    assert graph.model_dump(mode="json") == before
    with pytest.raises(ValueError):
        coordinator.review_with_steward(StewardDecision(assessment="bad", operation=StewardOperation.SHIFT_FOCUS, destination_node_id="A3", reason="tool"))


def test_steward_authority_has_no_local_creation_or_tool_operations() -> None:
    with pytest.raises(ValueError):
        StewardDecision.model_validate({"assessment": "x", "operation": "add_hypothesis", "reason": "x"})
    with pytest.raises(ValueError):
        InvestigatorUpdate.model_validate({"operation": "archive", "focus_node_id": "H1"})


def test_generalize_and_stop_preserve_graph_and_history() -> None:
    coordinator = GraphInvestigationCoordinator(scenario_graph(), InvestigationFocus(node_id="H1.1.1"))
    coordinator.review_with_steward(StewardDecision(assessment="Use broader region.", operation=StewardOperation.GENERALIZE, target_node_id="H1.1.1", reason="Child evidence is weak."))
    assert coordinator.focus.node_id == "H1.1"
    assert "H1.1.1" in coordinator.graph.nodes
    assert coordinator.graph.nodes["H1.1"].status is GraphStatus.ACTIVE
    previous = coordinator.history.previous
    coordinator.review_with_steward(StewardDecision(assessment="No useful frontier.", operation=StewardOperation.STOP_UNRESOLVED, reason="Uncertainty remains.", important_unresolved_ids=["U1"]))
    assert coordinator.stopped is True
    assert coordinator.graph.nodes["U1"].id == previous.graph.nodes["U1"].id
    assert coordinator.history.current.graph.model_dump(mode="json") == coordinator.graph.model_dump(mode="json")
