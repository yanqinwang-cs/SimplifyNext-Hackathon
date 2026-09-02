import json
import time
import threading
import urllib.request
from pathlib import Path

import pytest

from investigator.cycle import InvestigatorTurnResponse, TurnSnapshot
from investigator.llm import ModelCallMetadata, ModelCallResult, ModelParseError
from investigator.services.evidence_requests import HumanEvidenceWorkflow
from investigator.models.evidence_request import EvidenceRequest
from investigator.sources import SourceRegistry
from investigator.services.production_runner import ProductionInvestigationRunner, StewardEnvelope, seed_demo_case
from investigator.graph import GraphNode, GraphNodeType
from investigator.state import CaseRepository
from investigator.cycle import CycleError, InvestigatorCycleCoordinator
from investigator.roles import InvestigationFocus
from investigator.graph import CaseGraph


class SequenceClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def call(self, input_data, output_schema):
        self.calls.append((input_data, output_schema))
        if not self.responses:
            raise AssertionError("unexpected model call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if not isinstance(response, output_schema):
            response = output_schema.model_validate(response)
        return ModelCallResult(
            parsed=response,
            metadata=ModelCallMetadata(provider="offline", model="fixture", latency_seconds=0.001, parse_success=True, input_tokens=11, output_tokens=7, finish_reason="stop"),
            raw_output=response.model_dump(mode="json"),
        )


def investigator_request() -> dict:
    return {
        "graph_updates": [],
        "next_step": {
            "type": "request_evidence",
            "target_uncertainty_id": "U1",
            "information_sought": "The assessment-period records.",
            "reason": "They may resolve the active uncertainty.",
            "expected_information_value": "They could distinguish the remaining explanations.",
        },
    }


def steward_stop() -> dict:
    return {
        "operation": "stop_unresolved",
        "assessment": "The remaining uncertainty cannot be resolved from the current record.",
        "reason": "The local evidence frontier is exhausted.",
        "important_unresolved_ids": ["U1"],
        "reopening_conditions": "Reopen if new records are supplied.",
    }


def investigator_graph_update(statement: str) -> dict:
    return {
        "graph_updates": [{"operation": "add_hypothesis", "statement": statement, "reason": "It is a bounded explanation worth preserving."}],
        "next_step": {"type": "continue_local", "reason": "The new hypothesis supports another local step."},
    }


def investigator_invalid_graph_update() -> dict:
    return {
        "graph_updates": [
            {"operation": "add_hypothesis", "statement": "A failed turn's valid preliminary update", "reason": "It would be useful if the turn completed."},
            {"operation": "add_proposition", "statement": "An invalid reference", "derived_from_node_ids": ["P999"], "reason": "This must fail atomically."},
        ],
        "next_step": {"type": "local_exhausted", "reason": "The invalid batch must not commit."},
    }


def test_seed_uses_six_visible_sources_without_graph_evidence(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"))
    seed_demo_case(workflow, "case-01")
    state = workflow.repository.load("case-01")
    assert {source.name for source in state.sources.values()} == {
        "blank_tutorial.md", "student_script.md", "marker_report.md", "invigilator_report.md", "assessment_rules.md", "assessment_logistics.md",
    }
    assert state.evidence == {}
    assert state.reasoning_graph is not None and not any(node.node_type.value == "evidence" for node in state.reasoning_graph.nodes.values())


def test_runner_waits_for_human_request_and_preserves_trace_metadata(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"))
    client = SequenceClient([investigator_request()])
    ProductionInvestigationRunner(workflow, client).run("case-01")
    state = workflow.repository.load("case-01")
    assert state.runtime_status == "WAITING_FOR_EVIDENCE"
    assert state.evidence_request_history[0].request_id == "R1"
    assert len(client.calls) == 1
    trace = next(item for item in workflow.get_traces("case-01") if item["event"] == "investigator_completed")
    assert trace["snapshot_case_revision"] == 0
    assert trace["snapshot_graph_node_ids"] == ["U1"]
    assert trace["active_reasoning_node_ids"] == ["U1"]
    assert trace["legal_graph_node_ids"] == ["U1"]
    completed = workflow.respond("case-01", "R1", {"request_id": "R1", "status": "fulfilled"}, [{"display_name": "human-record.txt", "content": "new material"}], expected_case_revision=1)
    assert completed.status.value == "fulfilled"
    assert workflow.repository.load("case-01").evidence == {}


def test_runner_can_resume_after_fulfilment_and_stop_via_steward(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"))
    first_client = SequenceClient([investigator_request()])
    ProductionInvestigationRunner(workflow, first_client).run("case-01")
    workflow.respond("case-01", "R1", {"request_id": "R1", "status": "fulfilled"}, [{"display_name": "human-record.txt", "content": "new material"}], expected_case_revision=1)

    # local_exhausted transitions to Steward; the second call returns a typed stop decision.
    second_client = SequenceClient([
        {"graph_updates": [], "next_step": {"type": "local_exhausted", "reason": "No further local work remains."}},
        steward_stop(),
    ])
    ProductionInvestigationRunner(workflow, second_client).run("case-01")
    state = workflow.repository.load("case-01")
    assert state.runtime_status == "STOPPED"
    steward_prompt = json.loads(second_client.calls[1][0])["output_contract"]
    assert 'TOP-LEVEL DISCRIMINATOR FIELD: exactly "operation"' in steward_prompt
    assert '"operation":"stop_unresolved"' in steward_prompt
    assert 'DO NOT USE "decision"' in steward_prompt
    assert [trace["actor"] for trace in state.trace_history if trace["event"].endswith("_completed")] == ["investigator", "investigator", "steward"]
    assert state.reasoning_graph is not None and state.focus_node_id == "U1"


def test_runner_preserves_raw_parse_failure_in_role_trace(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"))
    bad = ModelParseError("invalid fixture JSON", raw_output='{"not": "a turn"}')
    client = SequenceClient([bad, bad])
    runner = ProductionInvestigationRunner(workflow, client)
    workflow.run_callback = lambda case_id, _workflow: runner.run(case_id)
    workflow.start_run("case-01")
    for _ in range(50):
        if workflow.get_workspace("case-01")["runtimeStatus"] == "FAILED":
            break
        time.sleep(0.01)
    traces = workflow.get_traces("case-01")
    failed = next(trace for trace in traces if trace["event"] == "investigator_failed")
    assert failed["raw_output"] == '{"not": "a turn"}'
    assert workflow.get_workspace("case-01")["runtimeStatus"] == "FAILED"


def test_workflow_run_creates_separate_audit_artifact(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"))
    client = SequenceClient([investigator_request()])
    runner = ProductionInvestigationRunner(workflow, client)
    workflow.run_callback = lambda case_id, _workflow: runner.run(case_id)
    workflow.start_run("case-01")
    for _ in range(50):
        if workflow.get_workspace("case-01")["runtimeStatus"] == "WAITING_FOR_EVIDENCE":
            break
        time.sleep(0.01)
    run = workflow.get_runs("case-01")[0]
    artifact = tmp_path / "cases" / "case-01" / "runs" / run["run_id"]
    assert run["run_id"] == "run_000001"
    assert (artifact / "raw_traces.jsonl").is_file()
    result = json.loads((artifact / "run_result.json").read_text())
    assert result["run_id"] == run["run_id"] and result["final_runtime_status"] == "WAITING_FOR_EVIDENCE"


def test_failed_retry_continues_current_canonical_state(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"))
    seed_demo_case(workflow, "case-01")
    attempts = []

    def callback(case_id, active):
        attempts.append(active.current_run_id(case_id))
        state = active.repository.load(case_id)
        if len(attempts) == 1:
            state.reasoning_graph.add_node(GraphNode(id="H1", node_type=GraphNodeType.HYPOTHESIS, statement="failed-run mutation"))
            SourceRegistry.register_raw_source(state, "post-checkpoint.txt", "stale source")
            active.repository.save(state)
            raise RuntimeError("failed after mutation")
        restored = active.repository.load(case_id)
        assert "H1" in restored.reasoning_graph.nodes
        assert "post-checkpoint.txt" in {source.name for source in restored.sources.values()}
        assert len(restored.sources) == 6
        active.set_runtime(case_id, "IDLE", "NONE")

    workflow.run_callback = callback
    workflow.start_run("case-01")
    for _ in range(50):
        if workflow.get_workspace("case-01")["runtimeStatus"] == "FAILED":
            break
        time.sleep(0.01)
    workflow.start_run("case-01")
    for _ in range(50):
        if workflow.get_workspace("case-01")["runtimeStatus"] == "IDLE":
            break
        time.sleep(0.01)
    assert attempts == ["run_000001", "run_000002"]
    assert len(workflow.get_runs("case-01")) == 2


def test_failed_later_turn_preserves_successful_turns_and_trace_revisions(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"))
    seed_demo_case(workflow, "case-01")
    state = workflow.repository.load("case-01")
    state.revision = 2
    workflow.repository.save(state)
    client = SequenceClient([
        investigator_graph_update("Successful turn one"),
        investigator_graph_update("Successful turn two"),
        investigator_invalid_graph_update(),
        investigator_invalid_graph_update(),
    ])
    runner = ProductionInvestigationRunner(workflow, client)
    workflow.run_callback = lambda case_id, _workflow: runner.run(case_id)
    workflow.start_run("case-01")
    for _ in range(100):
        if workflow.get_workspace("case-01")["runtimeStatus"] == "FAILED":
            break
        time.sleep(0.01)

    final_state = workflow.repository.load("case-01")
    assert final_state.revision == 4
    statements = {node.statement for node in final_state.reasoning_graph.nodes.values()}
    assert {"Successful turn one", "Successful turn two"} <= statements
    assert "A failed turn's valid preliminary update" not in statements

    investigator_traces = [trace for trace in workflow.get_traces("case-01") if trace.get("actor") == "investigator"]
    assert [trace["turn_start_revision"] for trace in investigator_traces] == [2, 3, 4]
    assert {trace["run_start_revision"] for trace in investigator_traces} == {2}
    failed = next(trace for trace in investigator_traces if trace["event"] == "investigator_failed")
    assert failed["failed_turn_start_revision"] == 4
    assert failed["final_case_revision"] == failed["latest_safe_revision"] == 4
    run_started = next(trace for trace in workflow.get_traces("case-01") if trace["event"] == "run_started")
    assert run_started["run_start_revision"] == run_started["latest_safe_revision"] == 2
    run_failed = next(event for event in workflow.workspace_events("case-01") if event["type"] == "run_failed")
    assert run_failed["final_case_revision"] == 4
    assert "preserved at revision 4" in run_failed["human_summary"]
    run = workflow.get_runs("case-01")[0]
    assert run["run_start_revision"] == 2
    assert run["latest_safe_revision"] == run["final_case_revision"] == run["final_committed_revision"] == 4


def test_reset_demo_case_replaces_stale_state_and_captures_verified_checkpoint(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"))
    seed_demo_case(workflow, "case-01")
    state = workflow.repository.load("case-01")
    state.reasoning_graph.add_node(GraphNode(id="E1", node_type=GraphNodeType.EVIDENCE, statement="stale"))
    state.revision = 7
    workflow.repository.save(state)
    from investigator.services.production_runner import reset_demo_case
    reset_demo_case(workflow, "case-01")
    restored = workflow.repository.load("case-01")
    assert set(restored.reasoning_graph.nodes) == {"U1"}
    assert restored.revision == 0 and restored.focus_node_id == "U1"
    assert set(restored.sources) == {f"S{number}" for number in range(20, 26)}
    assert restored.clean_checkpoint["signature"]


def test_source_registry_divergence_rejects_snapshot_before_apply(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"))
    seed_demo_case(workflow, "case-01")
    state = workflow.repository.load("case-01")
    coordinator = InvestigatorCycleCoordinator(state.reasoning_graph, InvestigationFocus(node_id=state.focus_node_id), case_revision=state.revision)
    snapshot = coordinator.turn_snapshot(workflow.readable_sources("case-01"), repository_revision=state.revision)
    state.sources["S20"].content = "mutated after prompt"
    workflow.repository.save(state)
    with pytest.raises(Exception, match="readable source registry changed"):
        workflow.assert_turn_snapshot_current("case-01", snapshot)


def test_partial_reset_contract_failure_happens_before_model_call(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"))
    seed_demo_case(workflow, "case-01")
    state = workflow.repository.load("case-01")
    state.reasoning_graph.add_node(GraphNode(id="E1", node_type=GraphNodeType.EVIDENCE, statement="stale"))
    workflow.repository.save(state)
    client = SequenceClient([investigator_request()])
    with pytest.raises(Exception, match="Clean checkpoint"):
        ProductionInvestigationRunner(workflow, client).run("case-01")
    assert client.calls == []


def test_real_run_http_boundary_uses_production_runner_and_contract_check(tmp_path: Path) -> None:
    from investigator.http_api import create_server

    client = SequenceClient([investigator_request()])
    server = create_server(tmp_path / "api-cases", port=0, run_callback=lambda case_id, workflow: ProductionInvestigationRunner(workflow, client).run(case_id))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        request = urllib.request.Request(f"{base}/api/cases/case-01/run", data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
        workspace = json.loads(urllib.request.urlopen(request).read())
        assert workspace["runtimeStatus"] in {"RUNNING_INVESTIGATOR", "WAITING_FOR_EVIDENCE"}
        for _ in range(100):
            workspace = json.loads(urllib.request.urlopen(f"{base}/api/cases/case-01/workspace").read())
            if workspace["runtimeStatus"] == "WAITING_FOR_EVIDENCE":
                break
            time.sleep(0.01)
        assert workspace["runtimeStatus"] == "WAITING_FOR_EVIDENCE"
        assert len(client.calls) == 1
        assert any(item.get("event") == "investigator_completed" for item in server.RequestHandlerClass.workflow.get_traces("case-01"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_turn_snapshot_rejects_repository_graph_divergence(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"))
    seed_demo_case(workflow, "case-01")
    state = workflow.repository.load("case-01")
    coordinator = InvestigatorCycleCoordinator(state.reasoning_graph, InvestigationFocus(node_id=state.focus_node_id), case_revision=state.revision)
    snapshot = coordinator.turn_snapshot(workflow.readable_sources("case-01"), repository_revision=state.revision)
    state.reasoning_graph.add_node(GraphNode(id="H1", node_type=GraphNodeType.HYPOTHESIS, statement="external mutation"))
    workflow.repository.save(state)
    with pytest.raises(Exception, match="canonical graph changed"):
        workflow.assert_turn_snapshot_current("case-01", snapshot)


def test_apply_turn_rejects_a_different_snapshot_baseline() -> None:
    graph = CaseGraph(case_id="snapshot", nodes={"U1": GraphNode(id="U1", node_type=GraphNodeType.UNCERTAINTY, statement="open")}, edges={})
    coordinator = InvestigatorCycleCoordinator(graph, InvestigationFocus(node_id="U1"))
    snapshot = coordinator.turn_snapshot()
    coordinator.graph.add_node(GraphNode(id="H1", node_type=GraphNodeType.HYPOTHESIS, statement="changed"))
    with pytest.raises(CycleError, match="baseline"):
        coordinator.apply_turn({"graph_updates": [], "next_step": {"type": "local_exhausted", "reason": "No useful local work remains."}}, snapshot=snapshot)
