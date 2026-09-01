import pytest
import inspect

from investigator.graph import CaseGraph, EdgeRelation, GraphEdge, GraphNode, GraphNodeType, GraphStatus
from investigator.roles import InvestigationFocus, StewardReviewContext
from experiments.steward_screen.trajectory import (
    IssueKind, IssueState, ScriptedProducer, StewardIssue, TerminalMode, TrajectoryFixture,
    issue_states, run_fixture,
)


def node(i, status=GraphStatus.ACTIVE):
    return GraphNode(id=i, node_type=GraphNodeType.HYPOTHESIS, statement=i, status=status)


def edge(a, rel, b):
    return GraphEdge(id=f"{a}_{rel.value.upper()}_{b}", source_id=a, target_id=b, relation=rel)


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


def test_dependency_states_progress_blocked_actionable_resolved():
    issues = [StewardIssue(issue_id="a", kind=IssueKind.RELEVANT_ARCHIVED, target_node_id="H3"), StewardIssue(issue_id="b", kind=IssueKind.NEGLECTED_ACTIVE, target_node_id="H3", depends_on_issue_ids=["a"])]
    from investigator.roles import GraphInvestigationCoordinator
    c = GraphInvestigationCoordinator(fixture(issues).graph, InvestigationFocus(node_id="H1"))
    assert issue_states(issues, c) == {"a": IssueState.ACTIONABLE, "b": IssueState.BLOCKED}
    c.review_with_steward(__import__("investigator.roles", fromlist=["ReactivateDecision"]).ReactivateDecision(assessment="a", reason="r", target_node_id="H3"))
    assert issue_states(issues, c)["a"] is IssueState.RESOLVED
    assert issue_states(issues, c)["b"] is IssueState.ACTIONABLE


def test_harmful_archive_and_premature_stop_are_visible():
    issues = [StewardIssue(issue_id="a", kind=IssueKind.RELEVANT_ARCHIVED, target_node_id="H3")]
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


def test_raw_and_fenced_json_use_production_normalization():
    f = fixture([])
    raw = '{"operation":"keep_focus","assessment":"a","reason":"r"}'
    fenced = "```json\n" + raw + "\n```"
    assert run_fixture(f, ScriptedProducer([raw])).termination == "quiescent"
    assert run_fixture(f, ScriptedProducer([fenced])).termination == "quiescent"
    assert "SCHEMA_FAILURE" in run_fixture(f, ScriptedProducer(["not json"])).failures
    assert "MULTIPLE_OPERATIONS_RETURNED" in run_fixture(f, ScriptedProducer([raw + raw])).failures


def test_must_remain_archived_and_derived_protection():
    f = fixture([StewardIssue(issue_id="a", kind=IssueKind.STALE_ACTIVE, target_node_id="H1")], must_remain_archived_node_ids={"H3"})
    result = run_fixture(f, ScriptedProducer([d("reactivate", target_node_id="H3")]))
    assert "HARMFUL_REACTIVATION" in result.failures
    parent = node("H1")
    child = node("H2")
    edges = [edge("H2", EdgeRelation.SPECIALIZES, "H1")]
    derived = fixture([StewardIssue(issue_id="a", kind=IssueKind.OVER_SPECIFIC_FOCUS, target_node_id="H2", parent_node_id="H1")], nodes={"H1": parent, "H2": child}, edges=edges, focus="H2")
    result = run_fixture(derived, ScriptedProducer([d("archive", target_node_id="H2", destination_node_id="H1")]))
    assert "HARMFUL_ARCHIVE" in result.failures


def test_producer_isolation_public_schema_contains_no_private_fields():
    private_names = {"issues", "terminal_mode", "must_remain_active_node_ids", "must_remain_archived_node_ids", "expected_state"}
    assert not private_names & set(__import__("experiments.steward_screen.trajectory", fromlist=["StewardObservation"]).StewardObservation.model_fields)
    assert set(inspect.signature(ScriptedProducer.__call__).parameters) == {"self", "prompt"}
    observation = fixture([]).observation()
    assert not private_names & set(observation.model_dump().keys())


def test_no_progress_loop_is_distinct_from_oscillation():
    nodes = {"H1": node("H1"), "H2": node("H2"), "H3": node("H3"), "H4": node("H4"), "H5": node("H5")}
    f = fixture([StewardIssue(issue_id="a", kind=IssueKind.STALE_ACTIVE, target_node_id="H4")], nodes=nodes, step_cap=4)
    outputs = [d("shift_focus", destination_node_id="H2"), d("shift_focus", destination_node_id="H3"), d("shift_focus", destination_node_id="H1"), d("shift_focus", destination_node_id="H5")]
    result = run_fixture(f, ScriptedProducer(outputs))
    assert "NO_PROGRESS_LOOP" in result.failures
    assert "OSCILLATION" not in result.failures


def test_oscillation_detected_when_focus_cycle_repeats_without_progress():
    nodes = {"H1": node("H1"), "H2": node("H2"), "H3": node("H3")}
    f = fixture([StewardIssue(issue_id="a", kind=IssueKind.STALE_ACTIVE, target_node_id="H3")], nodes=nodes, step_cap=5)
    outputs = [d("shift_focus", destination_node_id="H2"), d("shift_focus", destination_node_id="H1"), d("shift_focus", destination_node_id="H2")]
    result = run_fixture(f, ScriptedProducer(outputs))
    assert "OSCILLATION" in result.failures


def test_step_cap_and_unknown_identifier_are_classified():
    f = fixture([StewardIssue(issue_id="a", kind=IssueKind.STALE_ACTIVE, target_node_id="H2")], step_cap=1)
    capped = run_fixture(f, ScriptedProducer([d("keep_focus")]))
    assert "STEP_CAP_WITH_PENDING_ISSUES" in capped.failures
    unknown = run_fixture(fixture([]), ScriptedProducer([d("shift_focus", destination_node_id="H999")]))
    assert "INVENTED_IDENTIFIER" in unknown.failures


def test_structural_basis_and_protected_initial_status_are_validated():
    with pytest.raises(ValueError):
        fixture([], required_edges=[("H1", "supports", "H2")])
    with pytest.raises(ValueError):
        fixture([], must_remain_active_node_ids={"H3"})
    with pytest.raises(ValueError):
        fixture([], must_remain_archived_node_ids={"H1"})
    with pytest.raises(ValueError):
        fixture([StewardIssue(issue_id="a", kind=IssueKind.CURRENT_FOCUS_STALE, target_node_id="H1", allowed_destination_node_ids=["H1"])])


def test_illegal_operation_failure_matrix():
    assert "ILLEGAL_SHIFT" in run_fixture(fixture([]), ScriptedProducer([d("shift_focus", destination_node_id="H3")])).failures
    assert "ILLEGAL_REACTIVATE" in run_fixture(fixture([]), ScriptedProducer([d("reactivate", target_node_id="H1")])).failures
    assert "BAD_GENERALIZATION" in run_fixture(fixture([]), ScriptedProducer([d("generalize", target_node_id="H1")])).failures
    assert "ILLEGAL_ARCHIVE" in run_fixture(fixture([]), ScriptedProducer([d("archive", target_node_id="H1")])).failures


def test_stale_keep_requires_two_unchanged_actionable_steps():
    f = fixture([StewardIssue(issue_id="a", kind=IssueKind.STALE_ACTIVE, target_node_id="H2")], step_cap=3)
    result = run_fixture(f, ScriptedProducer([d("keep_focus"), d("keep_focus")]))
    assert "STALE_KEEP_LOOP" in result.failures
    assert result.steps[0]["classification"] == "NEUTRAL"


def test_valid_stop_requires_resolved_issues():
    context = StewardReviewContext(global_frontier_assessed=True, local_frontier_exhausted=True, active_unresolved_ids=["U1"])
    nodes = {"H1": node("H1"), "H2": node("H2"), "U1": GraphNode(id="U1", node_type=GraphNodeType.UNCERTAINTY, statement="u")}
    f = fixture([StewardIssue(issue_id="a", kind=IssueKind.STALE_ACTIVE, target_node_id="H2")], nodes=nodes, terminal_mode=TerminalMode.STOP_UNRESOLVED, review_context=context, step_cap=2)
    result = run_fixture(f, ScriptedProducer([d("archive", target_node_id="H2"), d("stop_unresolved", important_unresolved_ids=["U1"], reopening_conditions="new evidence")]))
    assert result.termination == "stopped"
    assert "PREMATURE_STOP" not in result.failures
