from pathlib import Path
import time

import pytest

from investigator.graph import GraphScope, GraphScopeType
from investigator.llm import ModelCallMetadata, ModelCallResult
from investigator.models import AssessmentSubject, Source, SourceType
from investigator.services.evidence_requests import HumanEvidenceWorkflow
from investigator.services.vnext_runner import VNextProductionRunner
from investigator.state import CaseRepository
from investigator.vnext import (
    AssessmentRulePreset,
    AssessmentStatus,
    Confidence,
    FurthestJustifiedConclusion,
    InvestigatorAssessment,
    InvestigatorProposal,
    SubjectAssessment,
    VNextInvestigationRunner,
    VNextRunInput,
    VNextRunValidationError,
    ViolationAssessment,
    ViolationDefinition,
)
from investigator.vnext.semantic import (
    InvestigatorSemanticAssessment,
    SemanticItem,
    SemanticItemKind,
    SemanticSubjectAssessment,
    SemanticViolationAssessment,
)


def source(source_id: str, subject_id: str) -> Source:
    """A record private to one subject, admitted through trusted subject scope."""
    return Source(
        id=source_id,
        name=f"record-{source_id}",
        source_type=SourceType.DOCUMENT,
        content=f"{subject_id} private record",
        metadata={"assessment_scope": {"scope_type": "subject", "subject_id": subject_id}},
    )


def preset() -> AssessmentRulePreset:
    return AssessmentRulePreset(
        preset_id="preset",
        violations=[
            ViolationDefinition(violation_id="V1", label="V1", rule_text="Rule 1", prohibited_conduct="Conduct 1"),
            ViolationDefinition(violation_id="V2", label="V2", rule_text="Rule 2", prohibited_conduct="Conduct 2"),
        ],
    )


def run_input(*, subjects: dict[str, AssessmentSubject] | None = None) -> VNextRunInput:
    return VNextRunInput(
        case_id="case-01",
        sources={"S1": source("S1", "subject_A"), "S2": source("S2", "subject_B")},
        subjects=subjects or {},
        rule_preset=preset(),
    )


def subject_assessment(
    subject_id: str, statuses: tuple[AssessmentStatus, AssessmentStatus], *, refs: tuple[str, str] = ("", "")
) -> SubjectAssessment:
    return SubjectAssessment(
        subject_id=subject_id,
        violation_assessments=[
            ViolationAssessment(
                violation_id=violation_id,
                status=status,
                supporting_node_ids=[ref] if ref else [],
                reasoning_summary=f"{subject_id}-{violation_id}",
                confidence=Confidence.HIGH,
            )
            for violation_id, status, ref in zip(("V1", "V2"), statuses, refs, strict=False)
        ],
        furthest_conclusion=FurthestJustifiedConclusion(
            statement=f"Conclusion for {subject_id}", based_on_violation_ids=["V1"], confidence=Confidence.HIGH
        ),
    )


def assessment(
    subject_assessments: list[SubjectAssessment], proposal: InvestigatorProposal | None = None
) -> InvestigatorAssessment:
    return InvestigatorAssessment(proposal=proposal or InvestigatorProposal(), subject_assessments=subject_assessments)


def subject_evidence_proposal() -> InvestigatorProposal:
    return InvestigatorProposal.model_validate(
        {
            "graph_updates": [
                {
                    "operation": "add_evidence",
                    "local_ref": "e_a",
                    "statement": "A evidence",
                    "source_ids": ["S1"],
                    "scope": GraphScope(scope_type=GraphScopeType.SUBJECT, subject_id="subject_A"),
                    "reason": "record A",
                },
                {
                    "operation": "add_evidence",
                    "local_ref": "e_b",
                    "statement": "B evidence",
                    "source_ids": ["S2"],
                    "scope": GraphScope(scope_type=GraphScopeType.SUBJECT, subject_id="subject_B"),
                    "reason": "record B",
                },
            ]
        }
    )


def test_two_subjects_keep_independent_violation_assessments() -> None:
    inputs = run_input(
        subjects={
            "subject_A": AssessmentSubject(subject_id="subject_A", display_name="Candidate A"),
            "subject_B": AssessmentSubject(subject_id="subject_B", display_name="Candidate B"),
        }
    )
    output = assessment(
        [
            subject_assessment(
                "subject_B", (AssessmentStatus.NOT_CURRENTLY_SUPPORTED, AssessmentStatus.CONFLICTED), refs=("", "e_b")
            ),
            subject_assessment(
                "subject_A", (AssessmentStatus.SUPPORTED, AssessmentStatus.NOT_CURRENTLY_SUPPORTED), refs=("e_a", "")
            ),
        ],
        subject_evidence_proposal(),
    )
    result = VNextInvestigationRunner(lambda _: output).run(inputs)
    assert [item.subject_id for item in result.subject_assessments] == ["subject_A", "subject_B"]
    assert [item.status for item in result.subject_assessments[0].violation_assessments] == [
        AssessmentStatus.SUPPORTED,
        AssessmentStatus.NOT_CURRENTLY_SUPPORTED,
    ]
    assert [item.status for item in result.subject_assessments[1].violation_assessments] == [
        AssessmentStatus.NOT_CURRENTLY_SUPPORTED,
        AssessmentStatus.CONFLICTED,
    ]
    assert result.metadata.subject_ids == ["subject_A", "subject_B"]


@pytest.mark.parametrize(
    "bad_subjects, message",
    [
        (["subject_A"], "missing subjects"),
        (["subject_A", "subject_B", "subject_X"], "unknown subjects"),
        (["subject_A", "subject_A"], "duplicate SubjectAssessment"),
    ],
)
def test_subject_coverage_is_exact(bad_subjects: list[str], message: str) -> None:
    inputs = run_input(
        subjects={key: AssessmentSubject(subject_id=key, display_name=key) for key in ("subject_A", "subject_B")}
    )
    with pytest.raises(VNextRunValidationError, match=message):
        VNextInvestigationRunner(
            lambda _: assessment(
                [
                    subject_assessment(key, (AssessmentStatus.SUPPORTED, AssessmentStatus.SUPPORTED))
                    for key in bad_subjects
                ]
            )
        ).run(inputs)


@pytest.mark.parametrize(
    "violations, message",
    [
        (["V1"], r"missing=\['V2'\]"),
        (["V1", "V1"], "duplicate violation"),
        (["V1", "V3"], r"unknown=\['V3'\]"),
    ],
)
def test_each_subject_has_exact_configured_violation_set(violations: list[str], message: str) -> None:
    inputs = run_input(
        subjects={
            "subject_A": AssessmentSubject(subject_id="subject_A", display_name="A"),
            "subject_B": AssessmentSubject(subject_id="subject_B", display_name="B"),
        }
    )
    invalid = SubjectAssessment(
        subject_id="subject_A",
        violation_assessments=[
            ViolationAssessment(
                violation_id=key,
                status=AssessmentStatus.SUPPORTED,
                reasoning_summary="reason",
                confidence=Confidence.HIGH,
            )
            for key in violations
        ],
        furthest_conclusion=FurthestJustifiedConclusion(statement="bounded", confidence=Confidence.HIGH),
    )
    valid_b = subject_assessment(
        "subject_B", (AssessmentStatus.NOT_CURRENTLY_SUPPORTED, AssessmentStatus.NOT_CURRENTLY_SUPPORTED)
    )
    with pytest.raises(VNextRunValidationError, match=message):
        VNextInvestigationRunner(lambda _: assessment([invalid, valid_b])).run(inputs)


def test_conclusion_cannot_reference_unknown_violation_for_one_subject() -> None:
    inputs = run_input(
        subjects={
            "subject_A": AssessmentSubject(subject_id="subject_A", display_name="A"),
            "subject_B": AssessmentSubject(subject_id="subject_B", display_name="B"),
        }
    )
    invalid = subject_assessment("subject_A", (AssessmentStatus.SUPPORTED, AssessmentStatus.SUPPORTED)).model_copy(
        update={
            "furthest_conclusion": FurthestJustifiedConclusion(
                statement="bounded", based_on_violation_ids=["V3"], confidence=Confidence.HIGH
            )
        }
    )
    with pytest.raises(VNextRunValidationError, match="subject_A.*unknown violation"):
        VNextInvestigationRunner(
            lambda _: assessment(
                [
                    invalid,
                    subject_assessment(
                        "subject_B",
                        (AssessmentStatus.NOT_CURRENTLY_SUPPORTED, AssessmentStatus.NOT_CURRENTLY_SUPPORTED),
                    ),
                ]
            )
        ).run(inputs)


def test_same_violation_id_across_subjects_and_no_evidence_subject_are_valid() -> None:
    subjects = {key: AssessmentSubject(subject_id=key, display_name=key) for key in ("subject_A", "subject_B")}
    output = assessment(
        [
            subject_assessment(
                "subject_A", (AssessmentStatus.SUPPORTED, AssessmentStatus.NOT_CURRENTLY_SUPPORTED), refs=("e_a", "")
            ),
            subject_assessment(
                "subject_B", (AssessmentStatus.NOT_CURRENTLY_SUPPORTED, AssessmentStatus.NOT_CURRENTLY_SUPPORTED)
            ),
        ],
        subject_evidence_proposal(),
    )
    result = VNextInvestigationRunner(lambda _: output).run(run_input(subjects=subjects))
    assert (
        len(result.subject_assessments[0].violation_assessments)
        == len(result.subject_assessments[1].violation_assessments)
        == 2
    )
    assert all(
        item.status is AssessmentStatus.NOT_CURRENTLY_SUPPORTED
        for item in result.subject_assessments[1].violation_assessments
    )


def test_subject_and_violation_output_order_is_deterministic() -> None:
    subjects = {key: AssessmentSubject(subject_id=key, display_name=key) for key in ("subject_A", "subject_B")}
    output = assessment(
        [
            subject_assessment(
                "subject_B", (AssessmentStatus.CONFLICTED, AssessmentStatus.SUPPORTED), refs=("e_b", "e_b")
            ),
            subject_assessment(
                "subject_A", (AssessmentStatus.SUPPORTED, AssessmentStatus.NOT_CURRENTLY_SUPPORTED), refs=("e_a", "")
            ),
        ],
        subject_evidence_proposal(),
    )
    result = VNextInvestigationRunner(lambda _: output).run(run_input(subjects=subjects))
    assert [item.subject_id for item in result.subject_assessments] == ["subject_A", "subject_B"]
    assert [item.violation_id for item in result.subject_assessments[0].violation_assessments] == ["V1", "V2"]


def test_node_references_resolve_inside_each_subject_assessment() -> None:
    proposal = subject_evidence_proposal()
    output = assessment(
        [
            subject_assessment(
                "subject_A", (AssessmentStatus.SUPPORTED, AssessmentStatus.NOT_CURRENTLY_SUPPORTED), refs=("e_a", "")
            ),
            subject_assessment(
                "subject_B", (AssessmentStatus.SUPPORTED, AssessmentStatus.NOT_CURRENTLY_SUPPORTED), refs=("e_b", "")
            ),
        ],
        proposal,
    )
    result = VNextInvestigationRunner(lambda _: output).run(
        run_input(
            subjects={key: AssessmentSubject(subject_id=key, display_name=key) for key in ("subject_A", "subject_B")}
        )
    )
    assert result.subject_assessments[0].violation_assessments[0].supporting_node_ids[0].startswith("node_")
    assert result.subject_assessments[1].violation_assessments[0].supporting_node_ids[0].startswith("node_")
    assert (
        result.subject_assessments[0].violation_assessments[0].supporting_node_ids
        != result.subject_assessments[1].violation_assessments[0].supporting_node_ids
    )


def semantic_assessment(*, mixed_item: bool = False) -> InvestigatorSemanticAssessment:
    """Model-shaped semantic reply; ``mixed_item`` adds one A-private plus B-private evidence item."""
    items = [
        SemanticItem(
            local_ref="e_a",
            kind=SemanticItemKind.EVIDENCE_STATEMENT,
            statement="A record says X.",
            basis_source_ids=["S1"],
        ),
        SemanticItem(
            local_ref="e_b",
            kind=SemanticItemKind.EVIDENCE_STATEMENT,
            statement="B record says Y.",
            basis_source_ids=["S2"],
        ),
        SemanticItem(
            local_ref="e_b_counter",
            kind=SemanticItemKind.EVIDENCE_STATEMENT,
            statement="B record says Z, which points away.",
            basis_source_ids=["S2"],
        ),
    ]
    if mixed_item:
        items.append(
            SemanticItem(
                local_ref="mixed",
                kind=SemanticItemKind.EVIDENCE_STATEMENT,
                statement="A combined observation.",
                basis_source_ids=["S1", "S2"],
            )
        )
    return InvestigatorSemanticAssessment(
        semantic_items=items,
        subject_assessments=[
            SemanticSubjectAssessment(
                subject_id="subject_A",
                violation_assessments=[
                    SemanticViolationAssessment(
                        violation_id="V1",
                        status=AssessmentStatus.SUPPORTED,
                        supporting_item_refs=["e_a"],
                        reasoning_summary="A bounded conclusion.",
                        confidence=Confidence.MODERATE,
                    ),
                    SemanticViolationAssessment(
                        violation_id="V2",
                        status=AssessmentStatus.NOT_CURRENTLY_SUPPORTED,
                        reasoning_summary="Not currently supported.",
                        confidence=Confidence.LOW,
                    ),
                ],
                furthest_conclusion=FurthestJustifiedConclusion(
                    statement="Conclusion for subject_A", based_on_violation_ids=["V1"], confidence=Confidence.MODERATE
                ),
            ),
            SemanticSubjectAssessment(
                subject_id="subject_B",
                violation_assessments=[
                    SemanticViolationAssessment(
                        violation_id="V1",
                        status=AssessmentStatus.NOT_CURRENTLY_SUPPORTED,
                        reasoning_summary="Not currently supported.",
                        confidence=Confidence.LOW,
                    ),
                    SemanticViolationAssessment(
                        violation_id="V2",
                        status=AssessmentStatus.CONFLICTED,
                        supporting_item_refs=["e_b"],
                        conflicting_item_refs=["e_b_counter"],
                        reasoning_summary="Conflicting material.",
                        confidence=Confidence.MODERATE,
                    ),
                ],
                furthest_conclusion=FurthestJustifiedConclusion(
                    statement="Conclusion for subject_B", based_on_violation_ids=["V2"], confidence=Confidence.MODERATE
                ),
            ),
        ],
    )


class TwoCallClient:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls: list[type[object]] = []

    def call(self, prompt: object, schema: type[object]) -> ModelCallResult:
        self.calls.append(schema)
        response = self.responses.pop(0)
        parsed = response if isinstance(response, schema) else schema.model_validate(response)
        return ModelCallResult(
            parsed=parsed,
            metadata=ModelCallMetadata(
                provider="offline",
                model="fixture",
                input_tokens=1,
                output_tokens=1,
                latency_seconds=0.001,
                parse_success=True,
                finish_reason="stop",
            ),
            raw_output=parsed.model_dump(mode="json"),
        )


def test_corrective_retry_preserves_multi_subject_assessments(tmp_path: Path) -> None:
    subjects = {key: AssessmentSubject(subject_id=key, display_name=key) for key in ("subject_A", "subject_B")}
    # The semantic path has no proposal-only corrective call: a rejected semantic item is retried as a clean semantic execution.
    client = TwoCallClient([semantic_assessment(mixed_item=True), semantic_assessment()])
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"))
    state = workflow.ensure_case("case-01")
    state.sources["S1"] = source("S1", "subject_A")
    state.sources["S2"] = source("S2", "subject_B")
    state.subjects = subjects
    workflow.repository.save(state)
    workflow.run_callback = VNextProductionRunner(client, preset_resolver=lambda _: preset()).run
    workflow.start_run("case-01")
    for _ in range(100):
        if workflow.get_workspace("case-01")["runtimeStatus"] in {"COMPLETED", "FAILED"}:
            break
        time.sleep(0.01)
    assert workflow.get_workspace("case-01")["runtimeStatus"] == "COMPLETED"
    assert client.calls == [InvestigatorSemanticAssessment, InvestigatorSemanticAssessment]
    completed = next(item for item in workflow.get_traces("case-01") if item["event"] == "vnext_completed")
    assert [item["subject_id"] for item in completed["result"]["subject_assessments"]] == ["subject_A", "subject_B"]
    assert completed["result"]["subject_assessments"][0]["violation_assessments"][0]["status"] == "supported"
