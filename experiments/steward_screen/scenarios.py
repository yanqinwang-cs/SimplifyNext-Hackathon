from copy import deepcopy

from investigator.graph import CaseGraph, EdgeRelation, EdgeStrength, GraphEdge, GraphNode, GraphNodeType, GraphStatus
from investigator.roles import InvestigationFocus, StewardReviewContext

from experiments.steward_screen.models import ExpectedState, StewardScenario


def _scenario(identifier: str, description: str, nodes: dict[str, GraphNode], edges: list[GraphEdge], focus: str, operation: str, target: str | None = None, destination: str | None = None, context: StewardReviewContext | None = None, stopped: bool = False, post_focus: str | None = None) -> StewardScenario:
    graph = CaseGraph(case_id=identifier, nodes=nodes, edges={edge.id: edge for edge in edges})
    archived = [node.id for node in nodes.values() if node.status is GraphStatus.ARCHIVED and not (operation == "reactivate" and node.id == target)]
    active_after = [target] if operation == "reactivate" and target else []
    return StewardScenario(scenario_id=identifier, description=description, graph=graph, focus=InvestigationFocus(node_id=focus), review_context=context, expected_operation=operation, expected_target_node_id=target, expected_destination_node_id=destination, expected_state=ExpectedState(focus_node_id=post_focus or destination or focus, archived_node_ids=archived + ([target] if operation == "archive" and target not in archived else []), active_node_ids=active_after, stopped=stopped))


def _node(identifier: str, kind: GraphNodeType, statement: str, status: GraphStatus = GraphStatus.ACTIVE) -> GraphNode:
    return GraphNode(id=identifier, node_type=kind, statement=statement, status=status)


def _edge(source: str, relation: EdgeRelation, target: str, strength: EdgeStrength | None = None) -> GraphEdge:
    return GraphEdge(id=f"{source}_{relation.value.upper()}_{target}", source_id=source, target_id=target, relation=relation, strength=strength)


def _context(unresolved: list[str] | None = None, **overrides) -> StewardReviewContext:
    unresolved = unresolved or ["U1"]
    values = {"global_frontier_assessed": True, "local_frontier_exhausted": True, "active_unresolved_ids": unresolved}
    values.update(overrides)
    return StewardReviewContext(**values)


def all_scenarios() -> list[StewardScenario]:
    n = GraphNodeType
    s = GraphStatus
    return [
        _scenario("K1", "The active H1 focus has direct E1 support and one active unresolved question; its local frontier remains useful.", {"E1": _node("E1", n.EVIDENCE, "Observed record",), "H1": _node("H1", n.HYPOTHESIS, "Some assistance may have contributed."), "U1": _node("U1", n.UNCERTAINTY, "Whether the record is complete.")}, [_edge("E1", EdgeRelation.SUPPORTS, "H1", EdgeStrength.DIRECT), _edge("U1", EdgeRelation.TARGETS, "H1")], "H1", "keep_focus"),
        _scenario("K2", "The active specific child H1.1 has direct support and a useful unresolved question; specificity alone is not a reason to generalize.", {"H1": _node("H1", n.HYPOTHESIS, "Assistance may have contributed."), "H1.1": _node("H1.1", n.HYPOTHESIS, "An unauthorised communication device may have been used."), "E2": _node("E2", n.EVIDENCE, "Device-related observation"), "U2": _node("U2", n.UNCERTAINTY, "Whether the device explanation fits.")}, [_edge("H1.1", EdgeRelation.SPECIALIZES, "H1"), _edge("E2", EdgeRelation.SUPPORTS, "H1.1", EdgeStrength.DIRECT), _edge("U2", EdgeRelation.TARGETS, "H1.1")], "H1.1", "keep_focus"),
        _scenario("S1", "P1 actively supports both H1 and H2. Focus has stayed at H1, while deterministic features surface P1 as a neglected cross-cutting proposition.", {"P1": _node("P1", n.PROPOSITION, "The recorded event is relevant to both explanations."), "H1": _node("H1", n.HYPOTHESIS, "Assistance explanation."), "H2": _node("H2", n.HYPOTHESIS, "Permitted-preparation explanation.")}, [_edge("P1", EdgeRelation.SUPPORTS, "H1"), _edge("P1", EdgeRelation.SUPPORTS, "H2")], "H1", "shift_focus", destination="P1"),
        _scenario("S2", "H2 is a separate active root with an unresolved question, while recent focus repeatedly visited H1. H2 is the neglected viable branch.", {"H1": _node("H1", n.HYPOTHESIS, "Assistance explanation."), "H2": _node("H2", n.HYPOTHESIS, "Permitted-preparation explanation."), "U2": _node("U2", n.UNCERTAINTY, "Whether preparation explains the gap.")}, [_edge("U2", EdgeRelation.TARGETS, "H2")], "H1", "shift_focus", destination="H2"),
        _scenario("G1", "The H1.1-specific local frontier is exhausted, but broader active H1 remains viable.", {"H1": _node("H1", n.HYPOTHESIS, "Broad assistance explanation."), "H1.1": _node("H1.1", n.HYPOTHESIS, "Over-specific mechanism.")}, [_edge("H1.1", EdgeRelation.SPECIALIZES, "H1")], "H1.1", "generalize", target="H1.1", post_focus="H1"),
        _scenario("G2", "The deepest H1.1.1 mechanism is unproductive; immediate parent H1.1 remains viable.", {"H1": _node("H1", n.HYPOTHESIS, "Broad explanation."), "H1.1": _node("H1.1", n.HYPOTHESIS, "Specific explanation."), "H1.1.1": _node("H1.1.1", n.HYPOTHESIS, "Deep mechanism.")}, [_edge("H1.1", EdgeRelation.SPECIALIZES, "H1"), _edge("H1.1.1", EdgeRelation.SPECIALIZES, "H1.1")], "H1.1.1", "generalize", target="H1.1.1", post_focus="H1.1"),
        _scenario("A1", "H1 is useful current focus. Separate active H2 has no support, conflict, unresolved issue, dependency, descendant, or useful frontier.", {"H1": _node("H1", n.HYPOTHESIS, "Current useful explanation."), "H2": _node("H2", n.HYPOTHESIS, "Stale unsupported branch.")}, [], "H1", "archive", target="H2"),
        _scenario("A2", "P2 is the current focus and is no longer consequential. Active P1 is the explicit useful destination after retirement.", {"P1": _node("P1", n.PROPOSITION, "Current relevant proposition."), "P2": _node("P2", n.PROPOSITION, "Retired proposition.")}, [], "P2", "archive", target="P2", destination="P1"),
        _scenario("R1", "Archived H2 is newly relevant because active P1 explicitly supports it as a live explanation.", {"H1": _node("H1", n.HYPOTHESIS, "Current explanation."), "H2": _node("H2", n.HYPOTHESIS, "Archived explanation.", s.ARCHIVED), "P1": _node("P1", n.PROPOSITION, "Newly relevant proposition.")}, [_edge("P1", EdgeRelation.SUPPORTS, "H2")], "H1", "reactivate", target="H2"),
        _scenario("R2", "Archived P2 is now explicitly needed because active H1 depends on the issue it represents.", {"H1": _node("H1", n.HYPOTHESIS, "Active explanation."), "P2": _node("P2", n.PROPOSITION, "Archived issue.", s.ARCHIVED)}, [_edge("H1", EdgeRelation.DEPENDS_ON, "P2")], "H1", "reactivate", target="P2"),
        _scenario("T1", "Only active unresolved U1 remains and the trusted frontier review is structurally exhausted.", {"H1": _node("H1", n.HYPOTHESIS, "Open explanation."), "U1": _node("U1", n.UNCERTAINTY, "Unresolved question.")}, [_edge("U1", EdgeRelation.TARGETS, "H1")], "H1", "stop_unresolved", context=_context(), stopped=True),
        _scenario("T2", "The same unresolved question remains, but active P1 supports both H1 and another root and is the neglected useful frontier. The Steward should redirect focus rather than stop.", {"H1": _node("H1", n.HYPOTHESIS, "Open explanation."), "H2": _node("H2", n.HYPOTHESIS, "Another live explanation."), "U1": _node("U1", n.UNCERTAINTY, "Unresolved question."), "P1": _node("P1", n.PROPOSITION, "Neglected useful proposition.")}, [_edge("U1", EdgeRelation.TARGETS, "H1"), _edge("P1", EdgeRelation.SUPPORTS, "H1"), _edge("P1", EdgeRelation.SUPPORTS, "H2")], "H1", "shift_focus", destination="P1"),
    ]


def expanded_scenarios() -> list[StewardScenario]:
    """Return the fixed cases plus ID/topology variants for calibration only."""
    base = all_scenarios()
    variants: list[StewardScenario] = []
    for index, original in enumerate(base, start=1):
        scenario = deepcopy(original)
        suffix = str(index + 10)
        scenario.scenario_id = f"{original.scenario_id}_V{index}"
        remap = {node_id: f"{node_id}.{suffix}" for node_id in scenario.graph.nodes}
        scenario.graph = CaseGraph(
            case_id=scenario.graph.case_id + "_variant",
            nodes={remap[node_id]: node.model_copy(update={"id": remap[node_id]}) for node_id, node in scenario.graph.nodes.items()},
            edges={
                f"{remap[edge.source_id]}_{edge.relation.value.upper()}_{remap[edge.target_id]}": edge.model_copy(update={"id": f"{remap[edge.source_id]}_{edge.relation.value.upper()}_{remap[edge.target_id]}", "source_id": remap[edge.source_id], "target_id": remap[edge.target_id]})
                for edge in scenario.graph.edges.values()
            },
        )
        scenario.focus = scenario.focus.model_copy(update={"node_id": remap[scenario.focus.node_id], "recent_node_ids": [remap.get(item, item) for item in scenario.focus.recent_node_ids]})
        scenario.expected_target_node_id = remap.get(scenario.expected_target_node_id, scenario.expected_target_node_id)
        scenario.expected_destination_node_id = remap.get(scenario.expected_destination_node_id, scenario.expected_destination_node_id)
        scenario.expected_state.focus_node_id = remap.get(scenario.expected_state.focus_node_id, scenario.expected_state.focus_node_id)
        scenario.expected_state.archived_node_ids = [remap.get(item, item) for item in scenario.expected_state.archived_node_ids]
        scenario.expected_state.active_node_ids = [remap.get(item, item) for item in scenario.expected_state.active_node_ids]
        if scenario.review_context is not None:
            scenario.review_context.active_unresolved_ids = [remap.get(item, item) for item in scenario.review_context.active_unresolved_ids]
        variants.append(scenario)
    return base + variants


def trajectory_scenarios() -> list[StewardScenario]:
    """Small sequential fixtures with several independent global issues."""
    n, s = GraphNodeType, GraphStatus
    scenario = _scenario("SEQ_A", "The current explanation remains active and depends on a proposition; a separate branch has no graph relation, while an archived explanation is linked to a new active proposition.", {"H1": _node("H1", n.HYPOTHESIS, "Current explanation."), "H2": _node("H2", n.HYPOTHESIS, "Stale branch."), "H3": _node("H3", n.HYPOTHESIS, "Previously archived explanation.", s.ARCHIVED), "P3": _node("P3", n.PROPOSITION, "New relevant proposition.")}, [_edge("P3", EdgeRelation.SUPPORTS, "H3"), _edge("H1", EdgeRelation.DEPENDS_ON, "P3")], "H1", "keep_focus")
    scenario.description = "The current explanation remains active; a separate branch has no graph relation, while an archived explanation is linked to a new active proposition."
    deep = deepcopy(all_scenarios()[5])
    deep.scenario_id = "SEQ_B"
    deep.description = "A deep active hypothesis has a narrower local explanation, while a separate active root remains available for later review."
    retiring = deepcopy(all_scenarios()[7])
    retiring.scenario_id = "SEQ_C"
    retiring.description = "The current proposition is no longer useful; another active proposition remains available and an archived explanation is linked to current material."
    return [scenario, deep, retiring]
