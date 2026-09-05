import json
import time
from pathlib import Path

import pytest

from investigator.graph import CaseGraph, GraphNode, GraphNodeType
from investigator.llm import ModelCallMetadata, ModelCallResult
from investigator.models.assessment import AssessmentSubject, SubjectRelationship
from investigator.models.source import Source, SourceType
from investigator.services.evidence_requests import HumanEvidenceWorkflow
from investigator.services.vnext_runner import VNextProductionRunner
from investigator.state import CaseRepository
from investigator.vnext import (
    AssessmentRulePreset,
    AssessmentStatus,
    Confidence,
    FurthestJustifiedConclusion,
    GraphWarden,
    InvestigatorAssessment,
    InvestigatorProposal,
    SubjectAssessment,
    VNextInvestigationRunner,
    VNextRunInput,
    ViolationAssessment,
    ViolationDefinition,
    WardenFailureClass,
    WardenValidationError,
    classify_warden_failure,
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


def _bad_proposal() -> InvestigatorProposal:
    return InvestigatorProposal.model_validate(
        {
            "graph_updates": [
                {
                    "operation": "add_proposition",
                    "local_ref": "relationship_claim",
                    "statement": "A relationship-level proposition.",
                    "derived_from_node_ids": ["S1", "S2"],
                    "scope": {"scope_type": "relationship", "relationship_ref": "R1"},
                    "reason": "Illustrate the invalid ancestry.",
                }
            ]
        }
    )


def _valid_assessment(proposal: InvestigatorProposal) -> InvestigatorAssessment:
    return InvestigatorAssessment(
        proposal=proposal,
        subject_assessments=[
            SubjectAssessment(
                subject_id=subject_id,
                violation_assessments=[
                    ViolationAssessment(
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


def test_private_to_relationship_derived_from_is_semantic_affecting() -> None:
    graph = CaseGraph(
        case_id="case5a-scope",
        nodes={
            source_id: GraphNode(
                id=source_id,
                node_type=GraphNodeType.SOURCE,
                statement=source_id,
                metadata={"assessment_scope": source.metadata["assessment_scope"]}
                if "assessment_scope" in source.metadata
                else {},
            )
            for source_id, source in _sources().items()
        },
        edges={},
    )
    with pytest.raises(WardenValidationError) as caught:
        GraphWarden(
            graph,
            _sources(),
            subjects=_subjects(),
            subject_relationships={
                "rel_AB": SubjectRelationship(
                    relationship_id="rel_AB",
                    subject_ids=["subject_A", "subject_B"],
                    relationship_type="joint_record",
                    source_ids=["S3"],
                )
            },
            relationship_refs={"R1": "rel_AB"},
            strict_relationship_refs=True,
        ).apply(_bad_proposal())
    issue = next(item for item in caught.value.issues if item.error_code == "INCOMPATIBLE_SCOPE")
    assert issue.relation == "derived_from"
    assert issue.source_scope == {"scope_type": "subject", "subject_id": "subject_A", "relationship_id": None, "relationship_ref": None}
    assert issue.target_scope == {"scope_type": "relationship", "subject_id": None, "relationship_id": "rel_AB", "relationship_ref": None}
    assert classify_warden_failure(caught.value) is WardenFailureClass.SEMANTIC_AFFECTING


class _SequenceClient:
    def __init__(self, responses: list[InvestigatorAssessment]) -> None:
        self.responses = list(responses)
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
            raw_output=response.model_dump(mode="json"),
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


def test_semantic_scope_failure_uses_one_fresh_full_retry_and_discards_first_graph(tmp_path: Path) -> None:
    client = _SequenceClient([_valid_assessment(_bad_proposal()), _valid_assessment(InvestigatorProposal())])
    workflow = _workflow(tmp_path, client)

    assert workflow.get_workspace("case-01")["runtimeStatus"] == "COMPLETED"
    assert len(client.calls) == 2
    assert "DETERMINISTIC RETRY CONSTRAINTS" not in client.calls[0]
    assert "DETERMINISTIC RETRY CONSTRAINTS" in client.calls[1]
    assert "relationship_claim" not in client.calls[1]
    run = workflow.get_workspace("case-01")["runs"][0]
    assert run["model_calls"] == 2
    assert run["clean_execution_retries"] == 1
    assert run["proposal_correction_calls"] == 0
    result = json.loads((tmp_path / "cases" / "case-01" / "runs" / run["run_id"] / "vnext_result.json").read_text())
    assert set(result["result"]["graph"]["nodes"]) == {"S1", "S2", "S3"}
    traces = workflow.get_traces("case-01")
    required = {"vnext_semantic_scope_retry_required", "vnext_clean_retry_started", "vnext_completed"}
    assert required.issubset({item["event"] for item in traces})
    retry = next(item for item in traces if item["event"] == "vnext_semantic_scope_retry_required")
    assert retry["failure_class"] == "SEMANTIC_AFFECTING"
    assert retry["retry_mode"] == "clean_execution"


def test_second_semantic_scope_failure_is_terminal_without_third_call_or_partial_graph(tmp_path: Path) -> None:
    client = _SequenceClient([_valid_assessment(_bad_proposal()), _valid_assessment(_bad_proposal())])
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
    with pytest.raises(WardenValidationError):
        VNextInvestigationRunner(lambda _: _valid_assessment(_bad_proposal())).run(_run_input())
