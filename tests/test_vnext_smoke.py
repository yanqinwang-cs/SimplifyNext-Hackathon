import pytest

from investigator.llm import ModelCallMetadata, MockModelClient, ModelParseError
from investigator.vnext import (
    AssessmentStatus,
    Confidence,
    FurthestJustifiedConclusion,
    InvestigatorAssessment,
    InvestigatorProposal,
    SubjectAssessment,
    VNextRunStatus,
    ViolationAssessment,
    WardenValidationError,
)
from investigator.vnext.smoke import build_prompt, run_smoke, smoke_run_input


def valid_assessment(*, bad_source: bool = False) -> InvestigatorAssessment:
    source_id = "S999" if bad_source else "S1"
    proposal = InvestigatorProposal.model_validate(
        {
            "graph_updates": [
                {
                    "operation": "add_evidence",
                    "local_ref": "possession",
                    "statement": "The student possessed prohibited smart eyewear.",
                    "source_ids": [source_id],
                    "reason": "Record the direct observation.",
                }
            ]
        }
    )
    return InvestigatorAssessment(
        proposal=proposal,
        violation_assessments=[
            ViolationAssessment(
                violation_id="unauthorized_device",
                status=AssessmentStatus.SUPPORTED,
                supporting_node_ids=["possession"],
                mitigating_node_ids=[],
                unresolved_points=["Whether the device was activated is unresolved."],
                reasoning_summary="Possession satisfies the configured rule.",
                confidence=Confidence.HIGH,
            )
        ],
        furthest_conclusion=FurthestJustifiedConclusion(
            statement="The unauthorized-device violation is supported.",
            based_on_violation_ids=["unauthorized_device"],
            confidence=Confidence.HIGH,
        ),
    )


def client_for(response: object) -> MockModelClient:
    return MockModelClient(
        response=response,  # type: ignore[arg-type]
        metadata=ModelCallMetadata(
            provider="mock",
            model="offline-fixture",
            input_tokens=40,
            output_tokens=20,
            latency_seconds=0.01,
            parse_success=True,
            finish_reason="end_turn",
        ),
    )


def test_smoke_prompt_contains_finite_contract_and_current_schema() -> None:
    prompt = build_prompt(smoke_run_input())
    assert "complete finite assessment" in prompt
    assert "every configured violation exactly once" in prompt
    assert "Do not ask for more evidence" in prompt
    assert "NOT_CURRENTLY_SUPPORTED" in prompt
    assert "stronger downstream conduct" in prompt
    assert "unauthorized_device" in prompt
    assert '"InvestigatorAssessment"' in prompt
    for forbidden in ("continue_local", "local_exhausted", "request_information", "request_enquiry", "request_steward_review", "stop_unresolved", "move_focus", "archive", "reactivate"):
        assert forbidden not in prompt


def test_smoke_uses_one_fake_model_call_and_completes_pipeline() -> None:
    client = client_for(valid_assessment())
    result, metadata, _ = run_smoke(client)

    assert len(client.calls) == 1
    assert client.calls[0][1] is InvestigatorAssessment
    assert result.status is VNextRunStatus.COMPLETED
    assert result.violation_assessments[0].status is AssessmentStatus.SUPPORTED
    assert result.metadata.proposal_update_count == 1
    assert metadata.input_tokens == 40
    assert metadata.output_tokens == 20


def test_smoke_malformed_model_output_fails_before_warden() -> None:
    client = client_for({"proposal": {}, "violation_assessments": [], "furthest_conclusion": {}})
    with pytest.raises(ModelParseError, match="InvestigatorAssessment"):
        run_smoke(client)
    assert len(client.calls) == 1


def test_smoke_warden_failure_is_surfaced_without_completed_result() -> None:
    client = client_for(valid_assessment(bad_source=True))
    with pytest.raises(WardenValidationError, match="unknown raw source ID"):
        run_smoke(client)
    assert len(client.calls) == 1


def test_sparse_fixture_has_same_single_run_contract() -> None:
    client = client_for(
        valid_assessment().model_copy(
            update={
                "proposal": InvestigatorProposal(),
                "subject_assessments": [SubjectAssessment(
                    subject_id="case_subject",
                    violation_assessments=[
                    ViolationAssessment(
                        violation_id="unauthorized_device",
                        status=AssessmentStatus.NOT_CURRENTLY_SUPPORTED,
                        supporting_node_ids=[],
                        reasoning_summary="The present sources do not support the violation.",
                        confidence=Confidence.LOW,
                    )
                    ],
                    furthest_conclusion=FurthestJustifiedConclusion(
                    statement="The violation is not currently supported by the present record.",
                    confidence=Confidence.LOW,
                    ),
                )],
            }
        )
    )
    result, _, _ = run_smoke(client, case="sparse")
    assert result.status is VNextRunStatus.COMPLETED
    assert result.violation_assessments[0].status is AssessmentStatus.NOT_CURRENTLY_SUPPORTED
    assert len(client.calls) == 1
