import pytest
from investigator.graph import CaseGraph, EdgeRelation, GraphEdge, GraphNode, GraphNodeType, GraphStatus, EpistemicStatus, propagate_epistemic_status
from investigator.models import EvidenceItem, EvidenceKind, Hypothesis, HypothesisOrigin, Source, SourceType, Uncertainty, UncertaintyKind
from investigator.state import CaseState


def node(identifier: str, kind: GraphNodeType, statement: str = "x") -> GraphNode:
    return GraphNode(id=identifier, node_type=kind, statement=statement)


def graph() -> CaseGraph:
    return CaseGraph(case_id="synthetic", nodes={
        "E1": node("E1", GraphNodeType.EVIDENCE, "Recorded score displayed as 40/40."),
        "P1": node("P1", GraphNodeType.PROPOSITION, "Recorded mark accurately reflects submitted answers."),
        "P2": node("P2", GraphNodeType.PROPOSITION, "A scoring-system error affected the recorded result."),
        "H1": node("H1", GraphNodeType.HYPOTHESIS, "Prohibited assistance contributed."),
        "H2": node("H2", GraphNodeType.HYPOTHESIS, "Performance is explained without prohibited assistance."),
        "H1.1": node("H1.1", GraphNodeType.HYPOTHESIS, "A narrower assistance explanation."),
        "U1": node("U1", GraphNodeType.UNCERTAINTY, "Is the recorded score valid?"),
    }, edges={
        "E1_SUPPORTS_P1": GraphEdge(id="E1_SUPPORTS_P1", source_id="E1", target_id="P1", relation=EdgeRelation.SUPPORTS),
        "P2_CONFLICTS_P1": GraphEdge(id="P2_CONFLICTS_P1", source_id="P2", target_id="P1", relation=EdgeRelation.CONFLICTS),
        "H1_DEPENDS_ON_P1": GraphEdge(id="H1_DEPENDS_ON_P1", source_id="H1", target_id="P1", relation=EdgeRelation.DEPENDS_ON),
        "H2_DEPENDS_ON_P1": GraphEdge(id="H2_DEPENDS_ON_P1", source_id="H2", target_id="P1", relation=EdgeRelation.DEPENDS_ON),
        "U1_TARGETS_P1": GraphEdge(id="U1_TARGETS_P1", source_id="U1", target_id="P1", relation=EdgeRelation.TARGETS),
        "H1.1_SPECIALIZES_H1": GraphEdge(id="H1.1_SPECIALIZES_H1", source_id="H1.1", target_id="H1", relation=EdgeRelation.SPECIALIZES),
    })


def test_graph_validates_direction_and_traverses() -> None:
    case = graph()
    assert [node.id for node in case.neighbors("P1")] == ["E1", "H1", "H2", "P2", "U1"]
    assert case.incoming("P1", EdgeRelation.SUPPORTS)[0].id == "E1_SUPPORTS_P1"
    assert case.incoming("H1", EdgeRelation.SPECIALIZES)[0].source_id == "H1.1"
    assert [item.id for item in case.ancestors("H1.1")] == ["H1"]
    assert [item.id for item in case.descendants("H1")] == ["H1.1"]
    assert "P2" in {item.id for item in case.neighborhood("P1", depth=1).nodes.values()}


def test_invalid_edges_dangling_types_and_specialization_cycles_fail() -> None:
    with pytest.raises(ValueError):
        CaseGraph(case_id="x", nodes={"H1": node("H1", GraphNodeType.HYPOTHESIS)}, edges={"bad": GraphEdge(id="bad", source_id="H1", target_id="H1", relation=EdgeRelation.SPECIALIZES)})
    with pytest.raises(ValueError):
        CaseGraph(case_id="x", nodes={"H1": node("H1", GraphNodeType.HYPOTHESIS), "E1": node("E1", GraphNodeType.EVIDENCE)}, edges={"bad": GraphEdge(id="bad", source_id="H1", target_id="E1", relation=EdgeRelation.SUPPORTS)})
    case = graph()
    with pytest.raises(ValueError):
        case.add_edge(GraphEdge(id="H1_SPECIALIZES_H1.1", source_id="H1", target_id="H1.1", relation=EdgeRelation.SPECIALIZES))


def test_archive_reactivate_active_subgraph_and_specialize() -> None:
    case = graph()
    case.archive_node("H1.1", "weak child")
    assert "H1.1" in case.nodes and "H1.1" not in case.active_subgraph().nodes
    assert "H1.1_SPECIALIZES_H1" in case.edges
    case.reactivate_node("H1.1")
    case.specialize("H2", node("H2.1", GraphNodeType.HYPOTHESIS), ["E1"])
    assert case.ancestors("H2.1")[0].id == "H2"


def test_archived_edges_are_ignored_by_active_queries() -> None:
    case = graph()
    case.archive_edge("E1_SUPPORTS_P1")
    assert "E1" not in {item.id for item in case.neighbors("P1")}
    assert "E1" in {item.id for item in case.neighbors("P1", active_only=False)}
    assert case.incoming("P1", EdgeRelation.SUPPORTS, active_only=True) == []
    assert case.outgoing("E1", active_only=True) == []
    assert "E1" not in case.neighborhood("P1").nodes
    assert "E1_SUPPORTS_P1" not in case.active_subgraph().edges


def test_single_active_specialization_parent_allows_archived_history() -> None:
    case = graph()
    case.archive_edge("H1.1_SPECIALIZES_H1")
    case.specialize("H2", node("H1.1", GraphNodeType.HYPOTHESIS), [])
    assert [item.id for item in case.ancestors("H1.1")] == ["H2"]
    with pytest.raises(ValueError):
        case.add_edge(GraphEdge(id="H1.1_SPECIALIZES_H1", source_id="H1.1", target_id="H1", relation=EdgeRelation.SPECIALIZES))


def test_canonical_graph_node_and_edge_ids() -> None:
    assert node("E1", GraphNodeType.EVIDENCE)
    assert node("A1_RELEASE", GraphNodeType.EVIDENCE)
    assert node("H1.2", GraphNodeType.HYPOTHESIS)
    assert node("P3.1", GraphNodeType.PROPOSITION)
    assert node("H1:U1", GraphNodeType.UNCERTAINTY)
    for identifier, kind in [("E1", GraphNodeType.HYPOTHESIS), ("P1", GraphNodeType.EVIDENCE), ("hello world", GraphNodeType.HYPOTHESIS), ("H1..2", GraphNodeType.HYPOTHESIS), ("H:U", GraphNodeType.UNCERTAINTY), ("A_RELEASE", GraphNodeType.EVIDENCE)]:
        with pytest.raises(ValueError):
            node(identifier, kind)
    with pytest.raises(ValueError):
        GraphEdge(id="not-an-edge", source_id="E1", target_id="P1", relation=EdgeRelation.SUPPORTS)
    assert case_edge_id("E1", EdgeRelation.SUPPORTS, "P1") == "E1_SUPPORTS_P1"


def case_edge_id(source: str, relation: EdgeRelation, target: str) -> str:
    from investigator.graph import make_edge_id
    return make_edge_id(source, relation, target)


def test_explicit_specialization_propagation_is_asymmetric() -> None:
    parents = {"H1.1": "H1", "H1.1.1": "H1.1", "H2.1": "H2"}
    statuses = {identifier: EpistemicStatus.UNRESOLVED for identifier in {"H1", "H1.1", "H1.1.1", "H2", "H2.1"}}
    established = propagate_epistemic_status(statuses, parents, "H1.1", EpistemicStatus.ESTABLISHED)
    assert established["H1"] is EpistemicStatus.ESTABLISHED
    assert established["H1.1.1"] is EpistemicStatus.UNRESOLVED
    rejected = propagate_epistemic_status(established, parents, "H1", EpistemicStatus.REJECTED)
    assert rejected["H1.1"] is EpistemicStatus.REJECTED
    assert rejected["H1.1.1"] is EpistemicStatus.REJECTED
    assert rejected["H2"] is EpistemicStatus.UNRESOLVED


def test_graph_json_round_trip_and_case_state_bridge() -> None:
    case = graph()
    loaded = CaseGraph.model_validate(case.model_dump(mode="json"))
    assert loaded.model_dump(mode="json") == case.model_dump(mode="json")


def test_case_state_bridge_preserves_ids_text_and_creates_semantic_edges() -> None:
    source = Source(id="source", name="source", source_type=SourceType.OTHER)
    state = CaseState(
        case_id="case", title="title", sources={"source": source},
        evidence={"E1": EvidenceItem(id="E1", source_id="source", raw_content="Original evidence", kind=EvidenceKind.OTHER), "A1_RELEASE": EvidenceItem(id="A1_RELEASE", source_id="source", raw_content="Released evidence", kind=EvidenceKind.OTHER)},
        hypotheses={"H1": Hypothesis(id="H1", statement="Parent", origin=HypothesisOrigin.HUMAN, supporting_evidence_ids=["E1"], unresolved_issue_ids=["U1"]), "H1.1": Hypothesis(id="H1.1", statement="Child", origin=HypothesisOrigin.AGENT_SUGGESTION, parent_hypothesis_id="H1")},
        uncertainties={"U1": Uncertainty(id="U1", kind=UncertaintyKind.UNKNOWN, description="Open question")},
    )
    bridged = CaseGraph.from_case_state(state)
    assert {node.node_type for node in bridged.nodes.values()} == {GraphNodeType.EVIDENCE, GraphNodeType.HYPOTHESIS, GraphNodeType.UNCERTAINTY}
    assert bridged.nodes["E1"].statement == "Original evidence"
    assert bridged.nodes["A1_RELEASE"].node_type is GraphNodeType.EVIDENCE
    assert "E1_SUPPORTS_H1" in bridged.edges
    assert "H1.1_SPECIALIZES_H1" in bridged.edges
    assert "U1_TARGETS_H1" in bridged.edges
    assert not any(node.node_type is GraphNodeType.PROPOSITION for node in bridged.nodes.values())
