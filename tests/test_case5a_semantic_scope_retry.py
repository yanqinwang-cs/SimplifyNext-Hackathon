import json
import time
from pathlib import Path

import pytest

from investigator.llm import ModelCallMetadata, ModelCallResult
from investigator.models.assessment import AssessmentSubject, SubjectRelationship
from investigator.models.source import Source, SourceType
from investigator.public_views import public_run_handle_for_instance
from investigator.services.evidence_requests import HumanEvidenceWorkflow
from investigator.services.vnext_runner import VNextProductionRunner
from investigator.state import CaseRepository
from investigator.vnext import AssessmentRulePreset, AssessmentStatus, Confidence, FurthestJustifiedConclusion, VNextRunInput, ViolationDefinition
from investigator.vnext.semantic import (
    InvestigatorSemanticAssessment,
    SemanticItem,
    SemanticItemKind,
    SemanticSubjectAssessment,
    SemanticValidationError,
    SemanticViolationAssessment,
    compile_semantic_assessment,
)


def _subjects() -> dict[str, AssessmentSubject]:
    return {
        "subject_A": AssessmentSubject(subject_id="subject_A", display_name="Student A"),
        "subject_B": AssessmentSubject(subject_id="subject_B", display_name="Student B"),
    }


def _sources() -> dict[str, Source]:
    return {
        "S1": Source(
            id="S1",
            name="student-a.md",
            source_type=SourceType.DOCUMENT,
            content="Student A record.",
            metadata={"assessment_scope": {"scope_type": "subject", "subject_id": "subject_A"}},
        ),
        "S2": Source(
            id="S2",
            name="student-b.md",
            source_type=SourceType.DOCUMENT,
            content="Student B record.",
            metadata={"assessment_scope": {"scope_type": "subject", "subject_id": "subject_B"}},
        ),
        "S3": Source(
            id="S3",
            name="joint-record.md",
            source_type=SourceType.DOCUMENT,
            content="Student A and Student B were recorded together.",
        ),
    }


def _preset() -> AssessmentRulePreset:
    return AssessmentRulePreset(
        preset_id="case5a-scope-test",
        violations=[ViolationDefinition(violation_id="V1", label="Conduct", rule_text="rule", prohibited_conduct="conduct")],
    )


def _run_input() -> VNextRunInput:
    return VNextRunInput(
        case_id="case5a-scope",
        sources=_sources(),
        subjects=_subjects(),
        subject_relationships={
            "rel_AB": SubjectRelationship(
                relationship_id="rel_AB",
                subject_ids=["subject_A", "subject_B"],
                relationship_type="joint_record",
                source_ids=["S3"],
            )
        },
        rule_preset=_preset(),
    )


def _semantic_assessment(*, semantic_items: list[object] | None = None) -> InvestigatorSemanticAssessment:
    return InvestigatorSemanticAssessment(
        semantic_items=semantic_items or [],
        subject_assessments=[
            SemanticSubjectAssessment(
                subject_id=subject_id,
                violation_assessments=[
                    SemanticViolationAssessment(
                        violation_id="V1",
                        status=AssessmentStatus.NOT_CURRENTLY_SUPPORTED,
                        reasoning_summary="The record does not currently support this finding.",
                        confidence=Confidence.LOW,
                    )
                ],
                furthest_conclusion=FurthestJustifiedConclusion(
                    statement="No supported conclusion is currently justified.", confidence=Confidence.LOW
                ),
            )
            for subject_id in ("subject_A", "subject_B")
        ],
    )


def _invalid_semantic_assessment() -> InvestigatorSemanticAssessment:
    return _semantic_assessment(
        semantic_items=[
            SemanticItem(
                local_ref="private_a",
                kind=SemanticItemKind.EVIDENCE_STATEMENT,
                statement="A private observation.",
                basis_source_ids=["S1"],
            ),
            SemanticItem(
                local_ref="private_b",
                kind=SemanticItemKind.EVIDENCE_STATEMENT,
                statement="B private observation.",
                basis_source_ids=["S2"],
            ),
            SemanticItem(
                local_ref="relationship_claim",
                kind=SemanticItemKind.PROPOSITION,
                statement="A relationship-level proposition.",
                about_subject_ids=["subject_A", "subject_B"],
                basis_item_refs=["private_a", "private_b"],
            ),
        ]
    )


class _SequenceClient:
    def __init__(self, responses: list[InvestigatorSemanticAssessment], raw_outputs: list[object] | None = None) -> None:
        self.responses = list(responses)
        self.raw_outputs = list(raw_outputs or [])
        self.calls: list[str] = []

    def call(self, prompt: object, schema: type[object]) -> ModelCallResult:
        self.calls.append(str(prompt))
        response = self.responses.pop(0)
        return ModelCallResult(
            parsed=schema.model_validate(response.model_dump(mode="python")),
            metadata=ModelCallMetadata(
                provider="offline",
                model="case5a-fixture",
                input_tokens=20,
                output_tokens=10,
                latency_seconds=0.001,
                parse_success=True,
                finish_reason="stop",
            ),
            raw_output=self.raw_outputs.pop(0) if self.raw_outputs else response.model_dump(mode="json"),
        )


def _workflow(tmp_path: Path, client: _SequenceClient) -> HumanEvidenceWorkflow:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode="vnext")
    state = workflow.ensure_case("case-01")
    state.subjects = _subjects()
    state.subject_relationships = {
        "rel_AB": SubjectRelationship(
            relationship_id="rel_AB",
            subject_ids=["subject_A", "subject_B"],
            relationship_type="joint_record",
            source_ids=["S3"],
        )
    }
    state.sources = _sources()
    workflow.repository.save(state)
    workflow.run_callback = VNextProductionRunner(client, preset_resolver=lambda _: _preset()).run
    workflow.start_run("case-01")
    for _ in range(200):
        if workflow.get_workspace("case-01")["runtimeStatus"] in {"COMPLETED", "FAILED"}:
            return workflow
        time.sleep(0.01)
    raise AssertionError("assessment did not reach a terminal state")


def test_private_to_relationship_semantic_construction_is_rejected_before_compile() -> None:
    with pytest.raises(SemanticValidationError, match="Keep separate subject-scoped propositions"):
        compile_semantic_assessment(_invalid_semantic_assessment(), _run_input())


def test_semantic_scope_failure_uses_one_fresh_full_retry_and_discards_first_graph(tmp_path: Path) -> None:
    client = _SequenceClient(
        [_invalid_semantic_assessment(), _semantic_assessment()],
        raw_outputs=["RAW_INITIAL_A", "RAW_SECOND_B"],
    )
    workflow = _workflow(tmp_path, client)

    assert workflow.get_workspace("case-01")["runtimeStatus"] == "COMPLETED"
    assert len(client.calls) == 2
    assert "DETERMINISTIC RETRY CONSTRAINTS" not in client.calls[0]
    assert "DETERMINISTIC RETRY CONSTRAINTS" in client.calls[1]
    assert "A relationship-level proposition." not in client.calls[1]
    run = workflow.get_workspace("case-01")["runs"][0]
    assert run["model_calls"] == 2
    assert run["clean_execution_retries"] == 1
    assert run["proposal_correction_calls"] == 0
    result = json.loads((tmp_path / "cases" / "case-01" / "runs" / run["run_id"] / "vnext_result.json").read_text())
    assert set(result["result"]["graph"]["nodes"]) == {"S1", "S2", "S3", "H1", "H2"}
    traces = workflow.get_traces("case-01")
    required = {"vnext_semantic_validation_failed", "vnext_attempt_failed", "vnext_completed"}
    assert required.issubset({item["event"] for item in traces})
    assert [item["event"] for item in traces if item["event"].startswith("vnext_")] == [
        "vnext_attempt_started",
        "vnext_model_call_started",
        "vnext_model_call_completed",
        "vnext_semantic_compilation_started",
        "vnext_semantic_validation_failed",
        "vnext_attempt_failed",
        "vnext_attempt_started",
        "vnext_model_call_started",
        "vnext_model_call_completed",
        "vnext_semantic_compilation_started",
        "vnext_semantic_compilation_completed",
        "vnext_completed",
    ]
    completed = [item for item in traces if item["event"] == "vnext_model_call_completed"]
    assert [item["model_call_number"] for item in completed] == [1, 2]
    assert [item["call_kind"] for item in completed] == ["semantic_initial", "semantic_clean_execution_retry"]
    assert [item["raw_output"] for item in completed] == ["RAW_INITIAL_A", "RAW_SECOND_B"]
    assert all("parsed_output" in item for item in completed)
    assert traces.index(completed[0]) < traces.index(completed[1])
    run = workflow.get_runs("case-01")[0]
    handle = public_run_handle_for_instance("case-01", run["run_id"], run["run_instance_id"])
    internal = [json.loads(line) for line in (tmp_path / "cases" / "case-01" / "runs" / run["run_id"] / "raw_traces.jsonl").read_text().splitlines()]
    exported = [json.loads(line) for line in workflow.audit_trace_file("case-01", run["run_id"], handle).decode().splitlines()]
    assert len(exported) == len(internal)
    assert [item["event"] for item in exported] == [item["event"] for item in internal]
    assert [item["raw_output"] for item in exported if item["event"] == "vnext_model_call_completed"] == ["RAW_INITIAL_A", "RAW_SECOND_B"]


def test_second_semantic_scope_failure_is_terminal_without_third_call_or_partial_graph(tmp_path: Path) -> None:
    client = _SequenceClient([_invalid_semantic_assessment(), _invalid_semantic_assessment()])
    workflow = _workflow(tmp_path, client)

    assert workflow.get_workspace("case-01")["runtimeStatus"] == "FAILED"
    assert len(client.calls) == 2
    run = workflow.get_workspace("case-01")["runs"][0]
    assert run["model_calls"] == 2
    assert run["clean_execution_retries"] == 1
    assert run["proposal_correction_calls"] == 0
    artifact = tmp_path / "cases" / "case-01" / "runs" / run["run_id"]
    assert not (artifact / "vnext_result.json").exists()
    assert workflow.repository.load("case-01").reasoning_graph is None


def test_clean_runner_rejects_demonstrated_scope_failure_before_any_graph_commit() -> None:
    with pytest.raises(SemanticValidationError):
        compile_semantic_assessment(_invalid_semantic_assessment(), _run_input())
