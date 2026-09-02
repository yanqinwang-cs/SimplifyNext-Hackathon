import json
import threading
import urllib.request
from pathlib import Path

import pytest

from investigator.cycle import CycleError, CycleFailureCode, CycleStatus, InvestigatorCycleCoordinator
from investigator.graph import CaseGraph, EdgeRelation, GraphEdge, GraphNode, GraphNodeType
from investigator.models.evidence_request import EvidenceRequestResponse
from investigator.roles import InvestigationFocus
from investigator.services.evidence_requests import EvidenceRequestConflict, HumanEvidenceWorkflow
from investigator.sources import SourceRegistry
from investigator.state import CaseRepository, CaseState
from investigator.cycle_prompt import build_investigator_cycle_prompt


def graph() -> CaseGraph:
    nodes = {identifier: GraphNode(id=identifier, node_type=kind, statement=identifier) for identifier, kind in {
        "E1": GraphNodeType.EVIDENCE, "P1": GraphNodeType.PROPOSITION, "H1": GraphNodeType.HYPOTHESIS, "U1": GraphNodeType.UNCERTAINTY,
    }.items()}
    edges = {
        "E1_SUPPORTS_P1": GraphEdge(id="E1_SUPPORTS_P1", source_id="E1", target_id="P1", relation=EdgeRelation.SUPPORTS),
        "H1_DEPENDS_ON_P1": GraphEdge(id="H1_DEPENDS_ON_P1", source_id="H1", target_id="P1", relation=EdgeRelation.DEPENDS_ON),
        "U1_TARGETS_H1": GraphEdge(id="U1_TARGETS_H1", source_id="U1", target_id="H1", relation=EdgeRelation.TARGETS),
    }
    return CaseGraph(case_id="case-01", nodes=nodes, edges=edges)


def request_payload() -> dict[str, str]:
    return {"type": "request_evidence", "target_uncertainty_id": "U1", "information_sought": "Available records for the assessment period.", "reason": "They may reduce the active uncertainty.", "expected_information_value": "They could distinguish the remaining explanations.",}


def test_request_evidence_is_not_an_action_and_enters_waiting_state() -> None:
    coordinator = InvestigatorCycleCoordinator(graph(), InvestigationFocus(node_id="H1"))
    state = coordinator.apply_turn({"graph_updates": [], "next_step": request_payload()})
    assert state.status is CycleStatus.WAITING_FOR_EVIDENCE
    assert state.evidence_request.request_id == "R1"
    assert "action_id" not in state.evidence_request.model_dump()

    with pytest.raises(CycleError) as error:
        coordinator.apply_turn({"graph_updates": [], "next_step": {"type": "local_exhausted", "reason": "Should wait."}})
    assert error.value.code is CycleFailureCode.EVIDENCE_REQUEST_ALREADY_PENDING

    completed = coordinator.complete_evidence_request({"request_id": "R1", "status": "fulfilled"}, ["S20"])
    assert completed.status is CycleStatus.LOCAL_ACTIVE
    assert completed.evidence_request is None
    assert completed.evidence_request_history[0].released_source_ids == ["S20"]


def test_workflow_allocates_request_ids_and_registers_raw_sources_only(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"))
    request = workflow.request_evidence("case-01", request_payload())
    assert request.request_id == "R1"
    with pytest.raises(EvidenceRequestConflict):
        workflow.request_evidence("case-01", request_payload())

    completed = workflow.respond("case-01", "R1", EvidenceRequestResponse(request_id="R1", status="fulfilled"), [{"display_name": "records.txt", "content": "raw material", "metadata": {"media_type": "text/plain"}}], expected_case_revision=1)
    state = workflow.repository.load("case-01")
    assert completed.released_source_ids == ["S20"]
    assert state.sources["S20"].name == "records.txt"
    assert state.sources["S20"].content == "raw material"
    assert state.evidence == {}
    assert state.evidence_request_history[0].status.value == "fulfilled"


def test_unavailable_is_workflow_outcome_and_model_revision_is_protected(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"))
    request = workflow.request_evidence("case-01", request_payload())
    workflow.set_model_revision_active("case-01", True)
    with pytest.raises(EvidenceRequestConflict):
        workflow.respond("case-01", request.request_id, {"request_id": request.request_id, "status": "unavailable"}, expected_case_revision=1)
    workflow.set_model_revision_active("case-01", False)
    completed = workflow.respond("case-01", request.request_id, {"request_id": request.request_id, "status": "unavailable", "note": "No records were available."}, [], expected_case_revision=1)
    assert completed.released_source_ids == []
    assert workflow.repository.load("case-01").sources == {}


def test_source_registry_assigns_application_owned_ids_without_graph_mutation(tmp_path: Path) -> None:
    repository = CaseRepository(tmp_path / "cases")
    state = CaseState(case_id="case", title="Case")
    source = SourceRegistry.register_raw_source(state, "upload.md", "raw")
    assert source.id == "S20"
    assert state.evidence == {}


def test_http_endpoints_return_workspace_and_accept_responses(tmp_path: Path) -> None:
    from investigator.http_api import create_server

    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"))
    workflow.request_evidence("case-01", request_payload())
    server = create_server(tmp_path / "api-cases", port=0)
    server.RequestHandlerClass.workflow = workflow
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        workspace = json.loads(urllib.request.urlopen(f"{base}/api/cases/case-01/workspace").read())
        assert workspace["status"] == "waiting_for_evidence"
        payload = json.dumps({"case_revision": 1, "sources": [{"display_name": "notes.txt", "content": "raw"}], "note": "Supplied by investigator."}).encode()
        request = urllib.request.Request(f"{base}/api/cases/case-01/evidence-requests/R1/fulfil", data=payload, headers={"Content-Type": "application/json"}, method="POST")
        response = json.loads(urllib.request.urlopen(request).read())
        assert response["completedRequest"]["status"] == "fulfilled"
        assert response["completedRequest"]["released_source_ids"] == ["S20"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_production_fulfilment_source_is_readable_by_next_investigator_prompt(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"))
    workflow.request_evidence("case-01", request_payload())
    token = "HUMAN LOOP SOURCE BODY TEST TOKEN 84217"
    completed = workflow.respond("case-01", "R1", {"request_id": "R1", "status": "fulfilled"}, [{"display_name": "human_marker_report.txt", "content": token}], expected_case_revision=1)
    sources = workflow.readable_sources("case-01")
    assert completed.status.value == "fulfilled"
    assert completed.released_source_ids == ["S20"]
    assert sources[-1].id == "S20" and sources[-1].content == token
    assert token in build_investigator_cycle_prompt(InvestigatorCycleCoordinator(graph(), InvestigationFocus(node_id="H1")).observation(sources))


def test_production_unavailable_preserves_workflow_without_source_or_evidence(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"))
    workflow.request_evidence("case-01", request_payload())
    completed = workflow.respond("case-01", "R1", {"request_id": "R1", "status": "unavailable", "note": "Not available."}, [], expected_case_revision=1)
    state = workflow.repository.load("case-01")
    assert completed.status.value == "unavailable"
    assert completed.released_source_ids == []
    assert state.sources == {} and state.evidence == {}
    assert state.evidence_request_history[0].note == "Not available."


def test_production_fulfilment_accepts_exactly_one_source(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"))
    workflow.request_evidence("case-01", request_payload())
    with pytest.raises(ValueError, match="exactly one"):
        workflow.respond("case-01", "R1", {"request_id": "R1", "status": "fulfilled"}, [{"display_name": "a.txt", "content": "a"}, {"display_name": "b.txt", "content": "b"}], expected_case_revision=1)


def test_workspace_exposes_case_and_truthful_runtime_states(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"))
    workflow.ensure_case("case-01")
    for runtime, actor in (("IDLE", "NONE"), ("RUNNING_INVESTIGATOR", "INVESTIGATOR"), ("RUNNING_STEWARD", "STEWARD"), ("FAILED", "NONE"), ("STOPPED", "NONE")):
        workflow.set_runtime("case-01", runtime, actor, failure_category="TEST_FAILURE" if runtime == "FAILED" else None, message="Failure detail" if runtime == "FAILED" else None, step=5 if runtime == "FAILED" else None)
        workspace = workflow.get_workspace("case-01")
        assert workspace["caseStatus"] == "ACTIVE"
        assert workspace["runtimeStatus"] == runtime
        assert workspace["currentActor"] == actor
    failed = workflow.get_workspace("case-01")
    workflow.set_runtime("case-01", "FAILED", "NONE", failure_category="TEST_FAILURE", message="Failure detail", step=5)
    failed = workflow.get_workspace("case-01")
    assert failed["lastError"] == {"failure_category": "TEST_FAILURE", "message": "Failure detail", "actor": "NONE", "step": 5}


def test_run_rejects_waiting_running_and_stopped_cases(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"))
    workflow.ensure_case("case-01")
    workflow.set_runtime("case-01", "RUNNING_INVESTIGATOR", "INVESTIGATOR")
    with pytest.raises(EvidenceRequestConflict, match="already running"):
        workflow.start_run("case-01")
    workflow.set_runtime("case-01", "RUNNING_STEWARD", "STEWARD")
    with pytest.raises(EvidenceRequestConflict, match="already running"):
        workflow.start_run("case-01")
    workflow.set_runtime("case-01", "WAITING_FOR_EVIDENCE", "NONE")
    with pytest.raises(EvidenceRequestConflict, match="pending"):
        workflow.start_run("case-01")
    workflow.set_runtime("case-01", "STOPPED", "NONE")
    with pytest.raises(EvidenceRequestConflict, match="Stopped"):
        workflow.start_run("case-01")


def test_run_stub_owns_runtime_and_persists_failure_or_waiting(tmp_path: Path) -> None:
    def fail(_case_id: str, _workflow: HumanEvidenceWorkflow) -> None:
        raise RuntimeError("deterministic run failure")

    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "fail-cases"), run_callback=fail)
    workspace = workflow.start_run("case-01")
    assert workspace["runtimeStatus"] in {"RUNNING_INVESTIGATOR", "FAILED"}
    import time
    for _ in range(20):
        if workflow.get_workspace("case-01")["runtimeStatus"] == "FAILED":
            break
        time.sleep(0.01)
    failed = workflow.get_workspace("case-01")
    assert failed["runtimeStatus"] == "FAILED" and failed["currentActor"] == "NONE"
    assert failed["lastError"]["message"] == "deterministic run failure"

    def wait(_case_id: str, active: HumanEvidenceWorkflow) -> None:
        active.request_evidence("case-02", request_payload())

    waiting_workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "wait-cases"), run_callback=wait)
    waiting_workflow.start_run("case-02")
    for _ in range(20):
        if waiting_workflow.get_workspace("case-02")["runtimeStatus"] == "WAITING_FOR_EVIDENCE":
            break
        time.sleep(0.01)
    assert waiting_workflow.get_workspace("case-02")["runtimeStatus"] == "WAITING_FOR_EVIDENCE"
