import time
from pathlib import Path

import pytest

from investigator.graph import CaseGraph, GraphNode, GraphNodeType, GraphStatus
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
    run = workflow.get_workspace("case-01")["runs"][0]
    assert run["model_calls"] == 2
    assert run["proposal_correction_calls"] == 1
    assert run["clean_execution_retries"] == 0
    result = workflow.get_traces("case-01")
    assert any(item["event"] == "vnext_corrective_retry_succeeded" for item in result)
    persisted = workflow.repository.load("case-01")
    completed = next(item for item in persisted.trace_history if item.get("event") == "vnext_completed")
    assert completed["result"]["subject_assessments"][0]["violation_assessments"][0]["reasoning_summary"] == "Distinctive semantic assessment preserved."


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
    assert issue["error_code"] == "INVALID_REFERENCE_TYPE"
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
    assert "Preserve unrelated valid graph updates" in prompt
    assert "Return only a corrected InvestigatorProposal" in prompt
    assert "COMPLETE LEGAL GRAPH OPERATION CONTRACT" in prompt
    assert "SUBJECT -> RELATIONSHIP" in prompt
    assert "HYPOTHESIS -> SUPPORTS -> HYPOTHESIS" in prompt


def test_corrective_prompt_prescribes_remove_only_for_self_derivation() -> None:
    proposal = InvestigatorProposal.model_validate({
        "graph_updates": [{"operation": "add_derivation", "derived_proposition_id": "p1", "source_node_id": "p1", "reason": "Connect."}]
    })
    assessment = _assessment(proposal)
    issue = ProposalValidationIssue(
        operation_index=3,
        field="add_derivation",
        error_code="SELF_DERIVATION",
        reference="p1",
        problem="Operation 3 derives proposition 'p1' from itself.",
        required_action=(
            "The proposition cannot be derived from itself. This proposition already declares its legal derivation "
            "basis through derived_from_node_ids. Remove only this redundant add_derivation operation. "
            "Preserve unrelated valid operations."
        ),
    )
    prompt = build_corrective_prompt(assessment, [issue])
    assert "SELF_DERIVATION" in prompt
    assert "operation_index\": 3" in prompt
    assert "p1" in prompt
    assert "cannot be derived from itself" in prompt
    assert "Remove only this redundant add_derivation operation" in prompt
    assert "Preserve unrelated valid operations" in prompt
    assert "PREVIOUS PROPOSAL" in prompt


def test_corrective_prompt_clarifies_field_relative_reference_hints() -> None:
    proposal = InvestigatorProposal.model_validate({
        "graph_updates": [{"operation": "add_derivation", "derived_proposition_id": "missing", "source_node_id": "E1", "reason": "Connect."}]
    })
    assessment = _assessment(proposal)
    issue = ProposalValidationIssue(
        operation_index=0,
        field="add_derivation.derived_proposition_id",
        error_code="UNRESOLVED_REFERENCE",
        reference="missing",
        allowed_types=["proposition"],
        construction_operation="add_proposition",
        construction_allowed_types=["evidence", "proposition"],
        known_illegal_refs={"E1": "evidence"},
        problem="The target proposition is missing.",
        required_action="Create the proposition with a legal construction basis.",
    )
    prompt = build_corrective_prompt(assessment, [issue])
    assert "allowed_types are for the failed field" in prompt
    assert "construction_allowed_types are legal inputs" in prompt
    assert "illegal for this specific field only" in prompt
    assert '"construction_allowed_types": [\n      "evidence"' in prompt


def test_corrective_prompt_preserves_semantics_and_removes_duplicate_relations() -> None:
    proposal = InvestigatorProposal.model_validate({
        "graph_updates": [{"operation": "add_derivation", "derived_proposition_id": "p1", "source_node_id": "E1", "reason": "Repeat relation."}]
    })
    assessment = _assessment(proposal)
    issue = ProposalValidationIssue(
        operation_index=4,
        field="add_derivation",
        error_code="DUPLICATE_RELATION",
        reference="E1",
        relation="derived_from",
        source="E1",
        target="p1",
        first_operation_index=3,
        problem="The relation already exists.",
        required_action="Remove only this redundant add_derivation operation. Preserve unrelated valid operations.",
    )
    prompt = build_corrective_prompt(assessment, [issue])
    assert "semantic assessment" in prompt and "frozen" in prompt
    assert "Do not add new factual predicates" in prompt
    assert "narrowest statement" in prompt
    assert "duplicate relation" in prompt
    assert "remove only the redundant operation" in prompt
    assert "PREVIOUS PROPOSAL" in prompt
    assert "DUPLICATE_RELATION" in prompt


def test_self_derivation_corrective_retry_changes_only_the_proposal(tmp_path: Path) -> None:
    initial = InvestigatorProposal.model_validate({
        "graph_updates": [
            {"operation": "add_evidence", "local_ref": "device_evidence", "statement": "Device record.", "source_ids": ["S1"], "reason": "Record source."},
            {"operation": "add_proposition", "local_ref": "p_device", "statement": "Device proposition.", "derived_from_node_ids": ["device_evidence"], "reason": "Create proposition."},
            {"operation": "add_derivation", "derived_proposition_id": "p_device", "source_node_id": "p_device", "reason": "Connect proposition."},
        ]
    })
    repaired = InvestigatorProposal.model_validate({
        "graph_updates": initial.graph_updates[:2]
    })
    original_assessment = _assessment(initial, support_ref="p_device")
    client = RepairClient([original_assessment, repaired])
    workflow = _workflow(tmp_path, client)

    assert workflow.get_workspace("case-01")["runtimeStatus"] == "COMPLETED"
    assert len(client.calls) == 2
    persisted = workflow.repository.load("case-01")
    completed = next(item for item in persisted.trace_history if item.get("event") == "vnext_completed")
    result = completed["result"]
    assert result["subject_assessments"][0]["furthest_conclusion"]["statement"] == original_assessment.furthest_conclusion.statement
    assert result["subject_assessments"][0]["violation_assessments"][0]["status"] == original_assessment.violation_assessments[0].status.value
    assert result["subject_assessments"][0]["violation_assessments"][0]["confidence"] == original_assessment.violation_assessments[0].confidence.value
    assert any(item["event"] == "vnext_corrective_retry_succeeded" for item in persisted.trace_history)


def test_second_pass_self_derivation_is_traced_and_does_not_get_third_call(tmp_path: Path) -> None:
    initial = InvestigatorProposal.model_validate({
        "graph_updates": [{"operation": "add_derivation", "derived_proposition_id": "p_device", "source_node_id": "E1", "reason": "Connect missing proposition."}]
    })
    repaired = InvestigatorProposal.model_validate({
        "graph_updates": [
            {"operation": "add_evidence", "local_ref": "device_evidence", "statement": "Device record.", "source_ids": ["S1"], "reason": "Record source."},
            {"operation": "add_proposition", "local_ref": "p_device", "statement": "Device proposition.", "derived_from_node_ids": ["device_evidence"], "reason": "Create proposition."},
            {"operation": "add_derivation", "derived_proposition_id": "p_device", "source_node_id": "p_device", "reason": "Connect proposition."},
        ]
    })
    client = RepairClient([_assessment(initial), repaired])
    workflow = _workflow(tmp_path, client)

    assert workflow.get_workspace("case-01")["runtimeStatus"] == "FAILED"
    assert len(client.calls) == 2
    traces = workflow.get_traces("case-01")
    second_failure = next(
        item for item in traces
        if item["event"] == "vnext_proposal_validation_failed" and item["attempt_number"] == 2
    )
    assert second_failure["runtime_status"] == "FAILED"
    assert second_failure["retry_mode"] == "corrective"
    assert second_failure["repairable"] is False
    issue = next(issue for issue in second_failure["validation_issues"] if issue["error_code"] == "SELF_DERIVATION")
    assert issue["operation_index"] == 2
    assert issue["reference"] == "p_device"
    assert "Remove only this redundant add_derivation operation" in issue["required_action"]
    assert any(item["event"] == "vnext_attempt_failed" for item in traces)
    assert not any(
        node.semantic_key == "p_device"
        for node in (workflow.repository.load("case-01").reasoning_graph or CaseGraph(case_id="case-01")).nodes.values()
    )


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


def test_warden_reports_status_separately_when_type_is_allowed() -> None:
    graph = CaseGraph(case_id="case-01", nodes={
        "P1": GraphNode(id="P1", node_type=GraphNodeType.PROPOSITION, statement="p"),
        "E1": GraphNode(id="E1", node_type=GraphNodeType.EVIDENCE, statement="e", status=GraphStatus.ARCHIVED),
    }, edges={})
    proposal = InvestigatorProposal.model_validate({
        "graph_updates": [{"operation": "add_derivation", "derived_proposition_id": "P1", "source_node_id": "E1", "reason": "Use source."}]
    })
    with pytest.raises(WardenValidationError) as caught:
        GraphWarden(graph).apply(proposal)
    issue = caught.value.issues[0]
    assert issue.error_code == "INVALID_REFERENCE_STATUS"
    assert issue.reference == "E1"
    assert issue.actual_type == "evidence"
    assert issue.actual_status == "archived"
    assert issue.allowed_statuses == ["active"]
    assert "active" in issue.required_action
