import pytest

from investigator.graph import CaseGraph, EdgeRelation, GraphEdge, GraphNode, GraphNodeType, GraphStatus
from investigator.roles import InvestigationFocus, StewardReviewContext
from experiments.steward_screen.trajectory import (
    IssueKind, ScriptedProducer, StewardIssue, TerminalMode, TrajectoryFixture,
    run_fixture,
)


def node(i, status=GraphStatus.ACTIVE):
    return GraphNode(id=i, node_type=GraphNodeType.HYPOTHESIS, statement=i, status=status)


def edge(a, rel, b):
    return GraphEdge(id=f"{a}-{rel.value}-{b}", source_id=a, target_id=b, relation=rel)


def fixture(issues, nodes=None, edges=None, focus="H1", **kw):
    nodes = nodes or {"H1": node("H1"), "H2": node("H2"), "H3": node("H3", GraphStatus.ARCHIVED)}
    return TrajectoryFixture(fixture_id="T", description="visible graph", graph=CaseGraph(case_id="T", nodes=nodes, edges={e.id: e for e in (edges or [])}), focus=InvestigationFocus(node_id=focus), issues=issues, **kw)


def d(op, **kw):
    return {"operation": op, "assessment": "a", "reason": "r", **kw}


def test_two_valid_orders_reach_quiescence():
    issues = [StewardIssue(issue_id="a", kind=IssueKind.STALE_ACTIVE, target_node_id="H2"), StewardIssue(issue_id="b", kind=IssueKind.RELEVANT_ARCHIVED, target_node_id="H3")]
    for outputs in ([d("archive", target_node_id="H2"), d("reactivate", target_node_id="H3")], [d("reactivate", target_node_id="H3"), d("archive", target_node_id="H2")]):
        result = run_fixture(fixture(issues), ScriptedProducer(outputs))
        assert result.termination == "quiescent"
        assert not result.failures


def test_dependency_blocks_shift_until_reactivation():
    issues = [StewardIssue(issue_id="a", kind=IssueKind.RELEVANT_ARCHIVED, target_node_id="H3"), StewardIssue(issue_id="b", kind=IssueKind.NEGLECTED_ACTIVE, target_node_id="H3", depends_on_issue_ids=["a"])]
    result = run_fixture(fixture(issues), ScriptedProducer([d("shift_focus", destination_node_id="H3")]))
    assert "ILLEGAL_SHIFT" in result.failures


def test_harmful_archive_and_premature_stop_are_visible():
    issues = [StewardIssue(issue_id="a", kind=IssueKind.NEGLECTED_ACTIVE, target_node_id="H2")]
    harmful = run_fixture(fixture(issues, must_remain_active_node_ids={"H2"}), ScriptedProducer([d("archive", target_node_id="H2")]))
    assert "HARMFUL_ARCHIVE" in harmful.failures
    context = StewardReviewContext(global_frontier_assessed=True, local_frontier_exhausted=True, active_unresolved_ids=["U1"])
    nodes = {"H1": node("H1"), "U1": GraphNode(id="U1", node_type=GraphNodeType.UNCERTAINTY, statement="u")}
    stopped = run_fixture(fixture([StewardIssue(issue_id="a", kind=IssueKind.STALE_ACTIVE, target_node_id="H1")], nodes=nodes, terminal_mode=TerminalMode.STOP_UNRESOLVED, review_context=context), ScriptedProducer([d("stop_unresolved", important_unresolved_ids=["U1"], reopening_conditions="new evidence")]))
    assert "PREMATURE_STOP" in stopped.failures


def test_fixture_rejects_bad_specialization_and_cycles():
    with pytest.raises(ValueError):
        fixture([StewardIssue(issue_id="a", kind=IssueKind.OVER_SPECIFIC_FOCUS, target_node_id="H1", parent_node_id="H2")])
    with pytest.raises(ValueError):
        fixture([StewardIssue(issue_id="a", kind=IssueKind.STALE_ACTIVE, target_node_id="H1", depends_on_issue_ids=["b"]), StewardIssue(issue_id="b", kind=IssueKind.STALE_ACTIVE, target_node_id="H2", depends_on_issue_ids=["a"])])


def test_null_and_multiple_outputs_are_rejected():
    f = fixture([])
    assert "NULL_OR_NO_DECISION" in run_fixture(f, ScriptedProducer([None])).failures
    assert "MULTIPLE_OPERATIONS_RETURNED" in run_fixture(f, ScriptedProducer([[d("keep_focus"), d("keep_focus")]])).failures
