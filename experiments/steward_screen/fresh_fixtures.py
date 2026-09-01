"""Frozen, structurally distinct sequential Steward fixtures."""
from investigator.graph import CaseGraph, EdgeRelation, GraphEdge, GraphNode, GraphNodeType, GraphStatus
from investigator.roles import InvestigationFocus, StewardReviewContext
from .trajectory import IssueKind, StewardIssue, TerminalMode, TrajectoryFixture


def _n(identifier, kind=GraphNodeType.HYPOTHESIS, status=GraphStatus.ACTIVE):
    return GraphNode(id=identifier, node_type=kind, statement=f"Academic-integrity case object {identifier}", status=status)


def _e(source, relation, target):
    edge = GraphEdge(id=f"{source}_{relation.value.upper()}_{target}", source_id=source, relation=relation, target_id=target)
    return edge


def _f(identifier, nodes, edges, focus, issues, **kwargs):
    graph = CaseGraph(case_id=identifier, nodes={n.id: n for n in nodes}, edges={e.id: e for e in edges})
    return TrajectoryFixture(fixture_id=identifier, description=f"Controlled sequential Steward graph {identifier}.", graph=graph, focus=InvestigationFocus(node_id=focus), issues=issues, required_edges=[(e.source_id, e.relation.value, e.target_id) for e in edges], step_cap=kwargs.pop("step_cap", 8), **kwargs)


def fresh_fixtures() -> list[TrajectoryFixture]:
    h = GraphNodeType.HYPOTHESIS
    seq1 = _f("SEQ1", [_n("H1"), _n("H2"), _n("H3")], [], "H1", [StewardIssue(issue_id="I1", kind=IssueKind.STALE_ACTIVE, target_node_id="H2"), StewardIssue(issue_id="I2", kind=IssueKind.NEGLECTED_ACTIVE, target_node_id="H3")], must_remain_active_node_ids={"H1", "H3"})
    seq2 = _f("SEQ2", [_n("H1"), _n("H2", status=GraphStatus.ARCHIVED), _n("P1", GraphNodeType.PROPOSITION)], [_e("P1", EdgeRelation.SUPPORTS, "H2")], "H1", [StewardIssue(issue_id="I1", kind=IssueKind.RELEVANT_ARCHIVED, target_node_id="H2"), StewardIssue(issue_id="I2", kind=IssueKind.NEGLECTED_ACTIVE, target_node_id="H2", depends_on_issue_ids=["I1"])])
    seq3 = _f("SEQ3", [_n("H1"), _n("H1.1"), _n("H2")], [_e("H1.1", EdgeRelation.SPECIALIZES, "H1")], "H1.1", [StewardIssue(issue_id="I1", kind=IssueKind.OVER_SPECIFIC_FOCUS, target_node_id="H1.1", parent_node_id="H1"), StewardIssue(issue_id="I2", kind=IssueKind.NEGLECTED_ACTIVE, target_node_id="H2")], must_remain_active_node_ids={"H1.1"})
    seq4 = _f("SEQ4", [_n("H1"), _n("H2")], [], "H1", [StewardIssue(issue_id="I1", kind=IssueKind.CURRENT_FOCUS_STALE, target_node_id="H1", allowed_destination_node_ids=["H2"])])
    seq5 = _f("SEQ5", [_n("H1"), _n("H2")], [], "H1", [StewardIssue(issue_id="I1", kind=IssueKind.STALE_ACTIVE, target_node_id="H2")], must_remain_active_node_ids={"H1"})
    context = StewardReviewContext(global_frontier_assessed=True, local_frontier_exhausted=True, active_unresolved_ids=["U1"])
    seq6 = _f("SEQ6", [_n("H1"), _n("U1", GraphNodeType.UNCERTAINTY)], [], "H1", [StewardIssue(issue_id="I1", kind=IssueKind.STALE_ACTIVE, target_node_id="H1")], terminal_mode=TerminalMode.STOP_UNRESOLVED, review_context=context)
    seq7 = _f("SEQ7", [_n("H1")], [], "H1", [])
    seq8 = _f("SEQ8", [_n("P2", GraphNodeType.PROPOSITION), _n("H1"), _n("H3", status=GraphStatus.ARCHIVED)], [_e("P2", EdgeRelation.SUPPORTS, "H1")], "H1", [StewardIssue(issue_id="I1", kind=IssueKind.STALE_ACTIVE, target_node_id="P2"), StewardIssue(issue_id="I2", kind=IssueKind.RELEVANT_ARCHIVED, target_node_id="H3"), StewardIssue(issue_id="I3", kind=IssueKind.NEGLECTED_ACTIVE, target_node_id="H3", depends_on_issue_ids=["I2"])])
    return [seq1, seq2, seq3, seq4, seq5, seq6, seq7, seq8]
