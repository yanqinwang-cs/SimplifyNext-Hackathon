import time
from pathlib import Path

import pytest

from investigator.graph import CaseGraph, GraphNode, GraphNodeType
from investigator.llm import ModelCallMetadata, ModelCallResult
from investigator.models.source import Source, SourceType
from investigator.services.evidence_requests import HumanEvidenceWorkflow
from investigator.services.vnext_runner import VNextProductionRunner
from investigator.state import CaseRepository
from investigator.vnext import (
    AssessmentStatus,
    Confidence,
    FurthestJustifiedConclusion,
    InvestigatorAssessment,
    InvestigatorProposal,
    ViolationAssessment,
)
from investigator.vnext.model import build_corrective_prompt
from investigator.vnext.presets import academic_integrity_core_preset
from investigator.vnext.warden import GraphWarden, ProposalValidationIssue, WardenValidationError


class RepairClient:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[object, type[object]]] = []

    def call(self, prompt: object, schema: type[object]) -> ModelCallResult:
        self.calls.append((prompt, schema))
        response = self.responses.pop(0)
        parsed = response if isinstance(response, schema) else schema.model_validate(response)
        return ModelCallResult(
            parsed=parsed,
            metadata=ModelCallMetadata(
                provider="offline", model="repair-fixture", input_tokens=10,
                output_tokens=8, latency_seconds=0.001, parse_success=True, finish_reason="stop",
            ),
            raw_output=parsed.model_dump(mode="json"),
        )


def _source() -> Source:
    return Source(id="S1", name="source-1", source_type=SourceType.DOCUMENT, content="A raw record")


def _assessment(proposal: InvestigatorProposal, *, support_ref: str = "p_device") -> InvestigatorAssessment:
    return InvestigatorAssessment(
        proposal=proposal,
        violation_assessments=[
            ViolationAssessment(
                violation_id="unauthorized_device",
                status=AssessmentStatus.SUPPORTED,
                supporting_node_ids=[support_ref],
                mitigating_node_ids=[],
                unresolved_points=[],
                reasoning_summary="Distinctive semantic assessment preserved.",
                confidence=Confidence.VERY_HIGH,
            ),
            ViolationAssessment(
                violation_id="unauthorized_external_communication",
                status=AssessmentStatus.CONFLICTED,
                supporting_node_ids=[], mitigating_node_ids=[], unresolved_points=[],
                reasoning_summary="Communication remains conflicted.", confidence=Confidence.MODERATE,
            ),
            *[
                ViolationAssessment(
                    violation_id=violation_id,
                    status=AssessmentStatus.NOT_CURRENTLY_SUPPORTED,
                    supporting_node_ids=[], mitigating_node_ids=[], unresolved_points=[],
                    reasoning_summary="No current support.", confidence=Confidence.LOW,
                )
                for violation_id in ("unauthorized_assistance", "prohibited_collaboration")
            ],
        ],
        furthest_conclusion=FurthestJustifiedConclusion(
            statement="Distinctive conclusion remains unchanged.",
            based_on_violation_ids=["unauthorized_device"], confidence=Confidence.VERY_HIGH,
        ),
    )


def _workflow(tmp_path: Path, client: RepairClient) -> HumanEvidenceWorkflow:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode="vnext")
    state = workflow.ensure_case("case-01")
    state.sources["S1"] = _source()
    workflow.repository.save(state)
    workflow.run_callback = VNextProductionRunner(client).run
    workflow.start_run("case-01")
    for _ in range(100):
        if workflow.get_workspace("case-01")["runtimeStatus"] in {"COMPLETED", "FAILED"}:
            break
        time.sleep(0.01)
    return workflow


def test_unknown_node_repair_is_proposal_only_and_preserves_assessment(tmp_path: Path) -> None:
    initial = InvestigatorProposal.model_validate({
        "graph_updates": [
            {"operation": "add_evidence", "local_ref": "device_evidence", "statement": "Device record.", "source_ids": ["S1"], "reason": "Record source."},
            {"operation": "add_proposition", "local_ref": "device_basis", "statement": "Device basis.", "derived_from_node_ids": ["device_evidence"], "reason": "Record basis."},
            {"operation": "add_derivation", "derived_proposition_id": "p_unauthorized_device_violation", "source_node_id": "device_basis", "reason": "Connect basis."},
        ]
    })
    repaired = InvestigatorProposal.model_validate({
        "graph_updates": [
            {"operation": "add_evidence", "local_ref": "device_evidence", "statement": "Device record.", "source_ids": ["S1"], "reason": "Record source."},
            {"operation": "add_proposition", "local_ref": "device_basis", "statement": "Device basis.", "derived_from_node_ids": ["device_evidence"], "reason": "Record basis."},
            {"operation": "add_proposition", "local_ref": "p_unauthorized_device_violation", "statement": "The device was prohibited.", "derived_from_node_ids": ["device_evidence"], "reason": "Create the intended proposition."},
            {"operation": "add_derivation", "derived_proposition_id": "p_unauthorized_device_violation", "source_node_id": "device_basis", "reason": "Connect basis."},
        ]
    })
    client = RepairClient([_assessment(initial, support_ref="p_unauthorized_device_violation"), repaired])
    workflow = _workflow(tmp_path, client)

    assert workflow.get_workspace("case-01")["runtimeStatus"] == "COMPLETED"
    assert len(client.calls) == 2
    assert client.calls[0][1] is InvestigatorAssessment
    assert client.calls[1][1] is InvestigatorProposal
    result = workflow.get_traces("case-01")
    assert any(item["event"] == "vnext_corrective_retry_succeeded" for item in result)
    persisted = workflow.repository.load("case-01")
    completed = next(item for item in persisted.trace_history if item.get("event") == "vnext_completed")
    assert completed["result"]["violation_assessments"][0]["reasoning_summary"] == "Distinctive semantic assessment preserved."


def test_wrong_node_type_feedback_is_prescriptive_and_repair_keeps_unrelated_update(tmp_path: Path) -> None:
    malformed = InvestigatorProposal.model_validate({
        "graph_updates": [
            {"operation": "add_evidence", "local_ref": "device_evidence", "statement": "Device record.", "source_ids": ["S1"], "reason": "Record source."},
            {"operation": "add_uncertainty", "local_ref": "u_device", "statement": "Device meaning remains uncertain.", "target_node_id": "device_evidence", "reason": "Track uncertainty."},
            {"operation": "add_proposition", "local_ref": "p_device", "statement": "Device proposition.", "derived_from_node_ids": ["u_device"], "reason": "Derive proposition."},
        ]
    })
    repaired = InvestigatorProposal.model_validate({
        "graph_updates": [
            {"operation": "add_evidence", "local_ref": "device_evidence", "statement": "Device record.", "source_ids": ["S1"], "reason": "Record source."},
            {"operation": "add_uncertainty", "local_ref": "u_device", "statement": "Device meaning remains uncertain.", "target_node_id": "device_evidence", "reason": "Track uncertainty."},
            {"operation": "add_proposition", "local_ref": "p_device", "statement": "Device proposition.", "derived_from_node_ids": ["device_evidence"], "reason": "Use legal evidence basis."},
        ]
    })
    client = RepairClient([_assessment(malformed), repaired])
    workflow = _workflow(tmp_path, client)

    assert workflow.get_workspace("case-01")["runtimeStatus"] == "COMPLETED"
    failed = next(item for item in workflow.get_traces("case-01") if item["event"] == "vnext_proposal_validation_failed")
    issue = failed["validation_issues"][0]
    assert issue["error_code"] == "INVALID_REFERENCE_TYPE_OR_STATUS"
    assert issue["reference"] == "u_device"
    assert issue["actual_type"] == "uncertainty"
    assert "evidence, proposition" in issue["required_action"]
    assert "u_device" in str(client.calls[1][0])


def test_corrective_prompt_contains_prior_proposal_and_prescriptive_constraints() -> None:
    proposal = InvestigatorProposal.model_validate({"graph_updates": [{"operation": "add_derivation", "derived_proposition_id": "p_missing", "source_node_id": "E1", "reason": "Connect."}]})
    assessment = _assessment(proposal)
    error = WardenValidationError(
        "rejected",
        issues=[
            ProposalValidationIssue(
                operation_index=0, error_code="UNRESOLVED_REFERENCE", reference="p_missing",
                problem="The node does not exist.",
                required_action="Create the intended proposition earlier, then reference its local_ref.",
            )
        ],
    )
    prompt = build_corrective_prompt(assessment, error.issues)
    assert "PREVIOUS PROPOSAL" in prompt
    assert "p_missing" in prompt
    assert "Do NOT re-investigate" in prompt
    assert "Preserve valid graph updates" in prompt
    assert "Return only a corrected InvestigatorProposal" in prompt


def test_failed_corrective_repair_does_not_get_a_third_model_call(tmp_path: Path) -> None:
    initial = InvestigatorProposal.model_validate({
        "graph_updates": [{"operation": "add_derivation", "derived_proposition_id": "p_missing", "source_node_id": "E1", "reason": "Connect."}]
    })
    client = RepairClient([_assessment(initial), initial])
    workflow = _workflow(tmp_path, client)

    assert workflow.get_workspace("case-01")["runtimeStatus"] == "FAILED"
    assert len(client.calls) == 2
    traces = workflow.get_traces("case-01")
    assert any(item["event"] == "vnext_proposal_validation_failed" for item in traces)
    assert any(item["attempt_number"] == 2 and item["event"] == "vnext_attempt_failed" for item in traces)


def test_warden_remains_strict_and_atomic_for_rejected_proposal() -> None:
    graph = CaseGraph(case_id="case-01", nodes={"U1": GraphNode(id="U1", node_type=GraphNodeType.UNCERTAINTY, statement="uncertain")}, edges={})
    proposal = InvestigatorProposal.model_validate({
        "graph_updates": [{"operation": "add_proposition", "local_ref": "p1", "statement": "Illegal.", "derived_from_node_ids": ["U1"], "reason": "Illegal basis."}]
    })
    with pytest.raises(WardenValidationError) as caught:
        GraphWarden(graph).apply(proposal)
    assert caught.value.issues[0].actual_type == "uncertainty"
    assert graph.nodes.keys() == {"U1"}
