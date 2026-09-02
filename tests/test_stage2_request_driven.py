import json
from pathlib import Path

from investigator.cycle import CycleStatus, InvestigatorCycleCoordinator
from investigator.graph import GraphNode, GraphNodeType
from investigator.graph import EdgeRelation, OperationSpecRegistry
from investigator.services import HumanEvidenceWorkflow
from investigator.roles import InvestigationFocus
from investigator.state import CaseRepository

from experiments.stage2_request_driven.environment import ControlledEvidenceEnvironment
from experiments.stage2_request_driven.fixtures import VISIBLE_FILENAMES, hidden_fixtures, initial_case
from experiments.stage2_request_driven.matcher import ControlledEvidenceMatcher
from experiments.stage2_request_driven.runner import apply_investigator_result, graph, main, manifest
from investigator.cycle_prompt import build_investigator_cycle_prompt
from investigator.roles.steward import StewardReviewContext


def test_visible_fixture_set_and_hidden_pool_are_separate() -> None:
    state = initial_case()
    assert [source.name for source in state.sources.values()] == list(VISIBLE_FILENAMES)
    assert len(state.sources) == 6
    assert "deterministic_timeline.md" not in {item.filename for item in hidden_fixtures()}


def test_visible_sources_are_readable_and_hidden_sources_are_not_in_investigator_prompt(tmp_path: Path) -> None:
    repository = CaseRepository(tmp_path / "cases")
    state = initial_case()
    repository.save(state)
    environment = ControlledEvidenceEnvironment(HumanEvidenceWorkflow(repository))
    observation = InvestigatorCycleCoordinator(graph(), InvestigationFocus(node_id="U1")).observation(environment.visible_sources("stage2b-case-01"))
    prompt = build_investigator_cycle_prompt(observation)

    for source in observation.visible_sources:
        assert source.id in prompt
        assert source.content in prompt
    for fixture in hidden_fixtures():
        assert fixture.filename not in prompt
        assert fixture.content not in prompt
    assert {source.id for source in observation.visible_sources} == set(state.sources)


def test_visible_match_uses_the_same_readable_source_set_and_release_adds_readability(tmp_path: Path) -> None:
    repository = CaseRepository(tmp_path / "cases")
    repository.save(initial_case())
    workflow = HumanEvidenceWorkflow(repository)
    environment = ControlledEvidenceEnvironment(workflow)
    coordinator = InvestigatorCycleCoordinator(graph(), InvestigationFocus(node_id="U1"))

    visible_observation = coordinator.observation(environment.visible_sources("stage2b-case-01"))
    visible_prompt = build_investigator_cycle_prompt(visible_observation)
    assert environment.matcher.visible_match("What concerns are in the assessment record?", [(s.name, s.content) for s in visible_observation.visible_sources]).answerable
    assert "S3" in visible_prompt and initial_case().sources["S3"].content in visible_prompt

    step = {"type": "request_evidence", "target_uncertainty_id": "U1", "information_sought": "What capabilities did the eyewear have?", "reason": "This may reduce uncertainty.", "expected_information_value": "It distinguishes explanations."}
    coordinator.apply_turn({"graph_updates": [], "next_step": step})
    pending = workflow.persist_pending_request("stage2b-case-01", coordinator.cycle.evidence_request)
    result = environment.respond("stage2b-case-01", pending)
    assert result.status == "fulfilled"
    coordinator.complete_evidence_request({"request_id": pending.request_id, "status": result.workflow_status, "released_source_ids": [result.source_id]})
    released_observation = coordinator.observation(environment.visible_sources("stage2b-case-01"))
    released_prompt = build_investigator_cycle_prompt(released_observation)
    assert result.source_id in {source.id for source in released_observation.visible_sources}
    assert result.source_id in released_prompt
    assert workflow.repository.load("stage2b-case-01").evidence == {}


def test_investigator_validation_is_atomic_and_reuses_coordinator_rules() -> None:
    coordinator = InvestigatorCycleCoordinator(graph(), InvestigationFocus(node_id="U1"))
    invalid = {"graph_updates": [{"operation": "add_proposition", "node_id": "P1", "statement": "A claim.", "derived_from_node_ids": ["U2"], "reason": "Record the claim."}], "next_step": {"type": "local_exhausted", "reason": "End the local turn."}}
    with __import__("pytest").raises(Exception, match="U2|invalid type"):
        coordinator.validate_turn(invalid)
    assert set(coordinator.graph.nodes) == {"U1"}

    valid = {"graph_updates": [
        {"operation": "add_hypothesis", "node_id": "H1", "statement": "A candidate explanation.", "reason": "Record a candidate."},
        {"operation": "add_uncertainty", "node_id": "U2", "statement": "What remains unknown?", "target_node_id": "H1", "reason": "Record the open question."},
        {"operation": "move_focus", "focus_node_id": "H1", "reason": "Review the new candidate."},
    ], "next_step": {"type": "local_exhausted", "reason": "End after the ordered local work."}}
    coordinator.validate_turn(valid)
    coordinator.apply_turn(valid)
    assert coordinator.focus.node_id == "H1"
    assert {"H1", "U2"} <= set(coordinator.graph.nodes)


def test_invisible_graph_reference_is_rejected_before_apply() -> None:
    coordinator = InvestigatorCycleCoordinator(graph(), InvestigationFocus(node_id="U1"))
    coordinator.graph.nodes["H9"] = GraphNode(id="H9", node_type=GraphNodeType.HYPOTHESIS, statement="Global only.")
    response = {"graph_updates": [{"operation": "move_focus", "focus_node_id": "H9", "reason": "Attempt an invalid move."}], "next_step": {"type": "local_exhausted", "reason": "End the turn."}}
    with __import__("pytest").raises(Exception, match="outside the active local region"):
        coordinator.validate_turn(response)
    assert coordinator.focus.node_id == "U1"


def test_steward_validator_uses_trusted_handoff_gate() -> None:
    coordinator = InvestigatorCycleCoordinator(graph(), InvestigationFocus(node_id="U1"))
    coordinator.apply_turn({"graph_updates": [], "next_step": {"type": "local_exhausted", "reason": "The local frontier is exhausted."}})
    context = StewardReviewContext(global_frontier_assessed=True, local_frontier_exhausted=True, available_action_ids=[], materially_usable_action_ids=[], active_unresolved_ids=["U1"], obvious_useful_region_remains=False)
    decision = {"operation": "stop_unresolved", "assessment": "No useful frontier remains.", "reason": "Trusted review supports handoff.", "important_unresolved_ids": ["U1"], "reopening_conditions": "New material."}
    coordinator.validate_steward_decision(decision, coordinator.cycle.case_revision, context)
    blocked = context.model_copy(update={"obvious_useful_region_remains": True})
    with __import__("pytest").raises(Exception, match="remaining useful graph region"):
        coordinator.validate_steward_decision(decision, coordinator.cycle.case_revision, blocked)


def test_matcher_handles_paraphrase_table_and_unknown() -> None:
    matcher = ControlledEvidenceMatcher()
    expected = {
        "Please provide permitted belongings recorded at entry": "entry_items",
        "What capabilities did the eyewear have": "device_examination",
        "Show eyewear usage activity logs": "smart_glasses_activity",
        "Provide network and internet connectivity": "network_connectivity",
        "Find the phone account pairing link": "device_account_linkage",
        "Any requests to an external online service": "external_ai_service",
        "Any communications with an outside assistant": "outside_assistant",
        "Compare prior assessed performance": "prior_work",
        "What teaching scope was covered": "module_scope",
        "Get the student's explanation": "student_clarification",
    }
    for text, key in expected.items():
        assert matcher.match(text).fixture.key == key
    assert matcher.match("A completely impossible category of material").fixture is None


def test_scripted_runner_uses_workflow_and_never_creates_hidden_initial_state(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"))
    state = initial_case()
    workflow.repository.save(state)
    environment = ControlledEvidenceEnvironment(workflow)
    request = workflow.request_evidence("stage2b-case-01", {"type": "request_evidence", "target_uncertainty_id": "U1", "information_sought": "permitted belongings at entry", "reason": "This reduces uncertainty.", "expected_information_value": "It distinguishes explanations."})
    result = environment.respond("stage2b-case-01", request)
    saved = workflow.repository.load("stage2b-case-01")
    assert result.status == "fulfilled"
    assert result.source_id == "S7"
    assert saved.evidence == {}
    assert all(item.metadata.get("fixture_key") not in {"hidden"} for item in saved.sources.values())


def test_live_bridge_uses_one_canonical_request_for_fulfilment(tmp_path: Path) -> None:
    repository = CaseRepository(tmp_path / "cases")
    repository.save(initial_case())
    workflow = HumanEvidenceWorkflow(repository)
    environment = ControlledEvidenceEnvironment(workflow)
    coordinator = InvestigatorCycleCoordinator(graph(), InvestigationFocus(node_id="U1"))
    step = {"type": "request_evidence", "target_uncertainty_id": "U1", "information_sought": "permitted belongings at entry", "reason": "This reduces uncertainty.", "expected_information_value": "It distinguishes explanations."}
    coordinator.apply_turn({"graph_updates": [], "next_step": step})
    canonical = workflow.persist_pending_request("stage2b-case-01", coordinator.cycle.evidence_request)
    pending = workflow.current_pending_request("stage2b-case-01")
    assert canonical.request_id == pending.request_id == coordinator.cycle.evidence_request.request_id == "R1"
    result = environment.respond("stage2b-case-01", pending)
    coordinator.complete_evidence_request({"request_id": pending.request_id, "status": result.status, "released_source_ids": [result.source_id] if result.source_id else []})
    assert result.request_id == "R1"
    assert coordinator.cycle.status is CycleStatus.LOCAL_ACTIVE
    assert workflow.repository.load("stage2b-case-01").evidence_request_history[0].status.value == "fulfilled"


def test_request_wait_preserves_tenure_nodes_and_visible_feedback(tmp_path: Path) -> None:
    repository = CaseRepository(tmp_path / "cases")
    repository.save(initial_case())
    workflow = HumanEvidenceWorkflow(repository)
    environment = ControlledEvidenceEnvironment(workflow)
    coordinator = InvestigatorCycleCoordinator(graph(), InvestigationFocus(node_id="U1"))
    request_step = {"type": "request_evidence", "target_uncertainty_id": "U1", "information_sought": "What specific concerns prompted this investigation?", "reason": "Existing concerns need to be reviewed.", "expected_information_value": "The current record may already address this need."}
    coordinator.apply_turn({"graph_updates": [
        {"operation": "add_hypothesis", "node_id": "H1", "statement": "A substantive explanation remains to be tested.", "reason": "Record the first candidate explanation."},
        {"operation": "add_hypothesis", "node_id": "H2", "statement": "An alternative substantive explanation remains to be tested.", "reason": "Record an alternative candidate explanation."},
        {"operation": "add_uncertainty", "node_id": "U2", "statement": "Which explanation best fits the current record?", "target_node_id": "H1", "reason": "Record a remaining uncertainty."},
    ], "next_step": request_step})
    assert set(coordinator.observation().local_graph.nodes) == {"U1", "U2", "H1", "H2"}
    workflow.persist_pending_request("stage2b-case-01", coordinator.cycle.evidence_request)
    pending = workflow.current_pending_request("stage2b-case-01")
    result = environment.respond("stage2b-case-01", pending)
    coordinator.complete_evidence_request({"request_id": pending.request_id, "status": result.workflow_status, "note": result.note})
    observation = coordinator.observation()
    assert set(observation.local_graph.nodes) == {"U1", "U2", "H1", "H2"}
    assert result.status == "no_new_source"
    assert result.source_id is None
    assert "already present" in (coordinator.observation().workflow_feedback or "")
    coordinator.apply_turn({"graph_updates": [], "next_step": {"type": "local_exhausted", "reason": "Review is complete for this bounded check."}})


def test_hidden_and_unavailable_requests_preserve_context(tmp_path: Path) -> None:
    repository = CaseRepository(tmp_path / "cases")
    repository.save(initial_case())
    workflow = HumanEvidenceWorkflow(repository)
    environment = ControlledEvidenceEnvironment(workflow)
    coordinator = InvestigatorCycleCoordinator(graph(), InvestigationFocus(node_id="U1"))
    for text, expected in (("Available evidence about whether the eyewear involved had electronic functionality.", "fulfilled"), ("A completely impossible category of material.", "unavailable")):
        step = {"type": "request_evidence", "target_uncertainty_id": "U1", "information_sought": text, "reason": "This may reduce uncertainty.", "expected_information_value": "It may distinguish explanations."}
        coordinator.apply_turn({"graph_updates": [], "next_step": step})
        workflow.persist_pending_request("stage2b-case-01", coordinator.cycle.evidence_request)
        pending = workflow.current_pending_request("stage2b-case-01")
        result = environment.respond("stage2b-case-01", pending)
        coordinator.complete_evidence_request({"request_id": pending.request_id, "status": result.workflow_status, "released_source_ids": [result.source_id] if result.source_id else []})
        assert result.status == expected
        assert set(coordinator.observation().local_graph.nodes) == {"U1"}


def test_visible_graph_equals_legal_graph_boundary_and_focus_rejects_invisible() -> None:
    coordinator = InvestigatorCycleCoordinator(graph(), InvestigationFocus(node_id="U1"))
    coordinator.graph.nodes["H9"] = GraphNode(id="H9", node_type=GraphNodeType.HYPOTHESIS, statement="Global but invisible hypothesis.")
    response = {"graph_updates": [
        {"operation": "add_hypothesis", "node_id": "H1", "statement": "First substantive explanation.", "reason": "Record a candidate."},
        {"operation": "add_hypothesis", "node_id": "H2", "statement": "Second substantive explanation.", "reason": "Record an alternative."},
        {"operation": "add_uncertainty", "node_id": "U2", "statement": "Which explanation fits?", "target_node_id": "H1", "reason": "Record the unresolved comparison."},
    ], "next_step": {"type": "continue_local", "reason": "The new semantic branch requires review."}}
    coordinator.apply_turn(response)
    observation = coordinator.observation()
    assert set(observation.local_graph.nodes) == coordinator.legal_node_ids()
    assert set(observation.local_graph.nodes) == {"U1", "U2", "H1", "H2"}
    coordinator.apply_turn({"graph_updates": [], "next_step": {"type": "request_evidence", "target_uncertainty_id": "U1", "information_sought": "The current concerns.", "reason": "Review the current record.", "expected_information_value": "It may reduce uncertainty."}})
    coordinator.complete_evidence_request({"request_id": "R1", "status": "unavailable", "note": "No additional source."})
    with __import__("pytest").raises(Exception, match="outside the active local region"):
        coordinator.apply_turn({"graph_updates": [{"operation": "move_focus", "focus_node_id": "H9", "reason": "Attempt an invisible move."}], "next_step": {"type": "local_exhausted", "reason": "Stop this invalid test turn."}})


def test_apply_failure_trace_retains_successful_model_result() -> None:
    coordinator = InvestigatorCycleCoordinator(graph(), InvestigationFocus(node_id="U1"))
    coordinator.graph.nodes["H9"] = GraphNode(id="H9", node_type=GraphNodeType.HYPOTHESIS, statement="Global but invisible hypothesis.")
    from investigator.llm import ModelCallMetadata, ModelCallResult
    from investigator.cycle import InvestigatorTurnResponse
    parsed = InvestigatorTurnResponse.model_validate({"graph_updates": [{"operation": "move_focus", "focus_node_id": "H9", "reason": "Attempt an invalid invisible move."}], "next_step": {"type": "local_exhausted", "reason": "The invalid move should fail."}})
    call = ModelCallResult(parsed=parsed, raw_output='{"graph_updates": [{"operation": "move_focus"}]}', metadata=ModelCallMetadata(provider="fake", model="fake", latency_seconds=0.01, parse_success=True))
    trace: dict[str, object] = {}
    try:
        apply_investigator_result(coordinator, call, trace)
    except Exception as exc:
        trace.update({"failure_category": "INVESTIGATOR_APPLY_FAILURE", "error": str(exc), "active_reasoning_node_ids": sorted(coordinator.observation().local_graph.nodes), "legal_graph_node_ids": sorted(coordinator.legal_node_ids())})
    assert trace["raw_output"] == call.raw_output
    assert trace["parsed_response"] == parsed.model_dump(mode="json")
    assert trace["graph_updates"]
    assert "outside the active local region" in trace["error"]


def test_manifest_declares_offline_boundaries() -> None:
    data = manifest()
    assert data["hidden_files_exposed_to_models"] is False
    assert data["hidden_fixture_count"] == 12
    assert data["step_caps"]["max_model_calls"] == 0


def test_operation_specs_exhaustively_whitelist_all_binary_type_combinations() -> None:
    expected = {
        EdgeRelation.DERIVED_FROM: {(GraphNodeType.PROPOSITION, GraphNodeType.EVIDENCE), (GraphNodeType.PROPOSITION, GraphNodeType.PROPOSITION)},
        EdgeRelation.SUPPORTS: {(source, target) for source in (GraphNodeType.EVIDENCE, GraphNodeType.PROPOSITION) for target in (GraphNodeType.PROPOSITION, GraphNodeType.HYPOTHESIS)},
        EdgeRelation.CONFLICTS: {(source, target) for source in (GraphNodeType.EVIDENCE, GraphNodeType.PROPOSITION) for target in (GraphNodeType.PROPOSITION, GraphNodeType.HYPOTHESIS)},
        EdgeRelation.TARGETS: {(GraphNodeType.UNCERTAINTY, target) for target in (GraphNodeType.EVIDENCE, GraphNodeType.PROPOSITION, GraphNodeType.HYPOTHESIS)},
        EdgeRelation.SPECIALIZES: {(GraphNodeType.HYPOTHESIS, GraphNodeType.HYPOTHESIS)},
        EdgeRelation.DEPENDS_ON: {(source, target) for source in (GraphNodeType.HYPOTHESIS, GraphNodeType.PROPOSITION) for target in (GraphNodeType.PROPOSITION, GraphNodeType.HYPOTHESIS)},
    }
    for relation, allowed in expected.items():
        matrix = OperationSpecRegistry.matrix(relation)
        assert set(pair for pair, permitted in matrix.items() if permitted) == allowed
        assert len(matrix) == 16


def test_request_open_is_distinct_from_targeted_request() -> None:
    coordinator = InvestigatorCycleCoordinator(graph(), InvestigationFocus(node_id="U1"))
    coordinator.apply_turn({"graph_updates": [], "next_step": {"type": "request_open", "reason": "The current record needs broader context."}})
    assert coordinator.cycle.evidence_request.target_uncertainty_id is None
    assert coordinator.cycle.evidence_request.expected_information_value is None


def test_dry_run_writes_no_model_calls(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["runner", "--dry-run", "--output-dir", str(tmp_path)])
    main()
    result = json.loads((tmp_path / "run_result.json").read_text())
    assert result == {"mode": "dry-run", "model_calls": 0}
