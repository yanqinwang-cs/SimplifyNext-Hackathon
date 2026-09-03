"""Finite, offline-testable vNext Investigator assessment pipeline.

A vNext run is finite and complete: it evaluates every configured violation
once, applies one typed proposal atomically, and then ends. Missing evidence
becomes an assessment state rather than another autonomous enquiry.
"""

from collections.abc import Callable, Mapping
from copy import deepcopy
from enum import Enum
import hashlib
import json

from pydantic import BaseModel, ConfigDict

from investigator.graph import CaseGraph, GraphNode, GraphNodeType
from investigator.models.source import Source
from investigator.state.case_state import CaseState
from investigator.vnext.models import (
    AssessmentRulePreset,
    FurthestJustifiedConclusion,
    InvestigatorAssessment,
    SubjectAssessment,
    VNextRunInput,
    ViolationAssessment,
)
from investigator.vnext.warden import GraphWarden


class VNextRunStatus(str, Enum):
    COMPLETED = "completed"


class VNextRunMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    rule_preset_id: str
    violation_ids: list[str]
    subject_ids: list[str]
    proposal_hash: str
    proposal_update_count: int = 0
    completion_state: VNextRunStatus = VNextRunStatus.COMPLETED


class VNextRunResult(BaseModel):
    """The structured terminal result of one clean vNext run."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    graph: CaseGraph
    subject_assessments: list[SubjectAssessment]
    status: VNextRunStatus = VNextRunStatus.COMPLETED
    metadata: VNextRunMetadata

    @property
    def violation_assessments(self) -> list[ViolationAssessment]:
        """Legacy single-subject read compatibility; subject_assessments is authoritative."""
        if len(self.subject_assessments) != 1:
            raise AttributeError("violation_assessments is available only for a single compatibility subject")
        return self.subject_assessments[0].violation_assessments

    @property
    def furthest_conclusion(self) -> FurthestJustifiedConclusion:
        """Legacy single-subject read compatibility; subject_assessments is authoritative."""
        if len(self.subject_assessments) != 1:
            raise AttributeError("furthest_conclusion is available only for a single compatibility subject")
        return self.subject_assessments[0].furthest_conclusion


def clean_reasoning_graph(case_id: str, sources: Mapping[str, Source]) -> CaseGraph:
    """Build fresh reasoning state from current persistent sources only."""
    nodes = {
        source_id: GraphNode(
            id=source_id,
            node_type=GraphNodeType.SOURCE,
            statement=source.name,
            semantic_key=source_id,
            canonical_id=source_id,
            metadata={"source_type": source.source_type.value, "readable": True},
        )
        for source_id, source in sorted(sources.items())
    }
    return CaseGraph(case_id=case_id, nodes=nodes, edges={})


def run_input_from_case_state(
    case_state: CaseState,
    rule_preset: AssessmentRulePreset,
    *,
    human_inputs: dict[str, object] | None = None,
) -> VNextRunInput:
    """Construct clean-run inputs without copying prior reasoning state."""
    return VNextRunInput.from_case_state(case_state, rule_preset, human_inputs=human_inputs)


class VNextRunValidationError(ValueError):
    """The finite assessment does not match its trusted run configuration."""


class VNextInvestigationRunner:
    """Run one injected Investigator assessment and one Warden application."""

    def __init__(self, investigator: Callable[[VNextRunInput], InvestigatorAssessment]) -> None:
        self.investigator = investigator

    def run(self, run_input: VNextRunInput) -> VNextRunResult:
        if not isinstance(run_input, VNextRunInput):
            raise TypeError("VNextInvestigationRunner.run requires a VNextRunInput")

        reasoning_graph = clean_reasoning_graph(run_input.case_id, run_input.sources)
        raw_assessment = self.investigator(run_input)
        try:
            assessment = raw_assessment if isinstance(raw_assessment, InvestigatorAssessment) else InvestigatorAssessment.model_validate(raw_assessment)
            assessment = InvestigatorAssessment.model_validate(assessment.model_dump(mode="python"))
            normalized_subjects = self._validate_assessment(assessment, run_input)
        except VNextRunValidationError:
            raise
        except Exception as exc:
            raise VNextRunValidationError(f"Invalid InvestigatorAssessment: {exc}") from exc

        warden = GraphWarden(reasoning_graph, run_input.sources)
        applied = warden.apply(assessment.proposal)
        resolved_subjects = []
        for subject in normalized_subjects:
            resolved_by_id = {
                item.violation_id: self._resolve_assessment_references(
                    item, applied.local_ref_resolution, applied.graph
                )
                for item in subject.violation_assessments
            }
            resolved_subjects.append(subject.model_copy(update={
                "violation_assessments": [
                    resolved_by_id[item.violation_id]
                    for item in run_input.rule_preset.violations
                ],
            }))
        proposal_hash = hashlib.sha256(
            json.dumps(assessment.proposal.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()
        metadata = VNextRunMetadata(
            case_id=run_input.case_id,
            rule_preset_id=run_input.rule_preset.preset_id,
            violation_ids=[item.violation_id for item in run_input.rule_preset.violations],
            subject_ids=[item.subject_id for item in normalized_subjects],
            proposal_hash=proposal_hash,
            proposal_update_count=len(assessment.proposal.graph_updates),
        )
        return VNextRunResult(
            graph=deepcopy(applied.graph),
            subject_assessments=resolved_subjects,
            metadata=metadata,
        )

    @staticmethod
    def _validate_assessment(assessment: InvestigatorAssessment, run_input: VNextRunInput) -> list[SubjectAssessment]:
        preset = run_input.rule_preset
        expected = [item.violation_id for item in preset.violations]
        expected_subjects = sorted(run_input.subjects) or ["case_subject"]
        actual_subjects = [item.subject_id for item in assessment.subject_assessments]
        if len(actual_subjects) != len(set(actual_subjects)):
            raise VNextRunValidationError("InvestigatorAssessment contains duplicate SubjectAssessment subject IDs")
        if set(actual_subjects) != set(expected_subjects):
            missing = sorted(set(expected_subjects) - set(actual_subjects))
            unknown = sorted(set(actual_subjects) - set(expected_subjects))
            if missing:
                raise VNextRunValidationError(f"Subject assessments are missing subjects: {missing}")
            raise VNextRunValidationError(f"InvestigatorAssessment contains unknown subjects: {unknown}")

        by_subject = {item.subject_id: item for item in assessment.subject_assessments}
        normalized: list[SubjectAssessment] = []
        for subject_id in expected_subjects:
            subject = by_subject[subject_id]
            actual = [item.violation_id for item in subject.violation_assessments]
            if len(actual) != len(set(actual)):
                raise VNextRunValidationError(f"Subject {subject_id!r} contains duplicate violation IDs")
            if set(actual) != set(expected):
                missing = sorted(set(expected) - set(actual))
                unknown = sorted(set(actual) - set(expected))
                raise VNextRunValidationError(
                    f"Subject {subject_id!r} must contain exactly the configured violations; missing={missing}, unknown={unknown}"
                )
            conclusion_ids = set(subject.furthest_conclusion.based_on_violation_ids)
            unknown_conclusion_ids = sorted(conclusion_ids - set(expected))
            if unknown_conclusion_ids:
                raise VNextRunValidationError(
                    f"Subject {subject_id!r} conclusion references unknown violation IDs: {unknown_conclusion_ids}"
                )
            normalized.append(subject.model_copy(update={
                "violation_assessments": [
                    next(item for item in subject.violation_assessments if item.violation_id == violation_id)
                    for violation_id in expected
                ]
            }))
        return normalized

    @staticmethod
    def _resolve_node_id(identifier: str, local_refs: Mapping[str, str], graph: CaseGraph) -> str:
        resolved = local_refs.get(identifier, identifier)
        if resolved not in graph.nodes:
            raise VNextRunValidationError(f"Assessment references unknown graph node ID: {identifier!r}")
        return resolved

    @classmethod
    def _resolve_assessment_references(
        cls,
        assessment: ViolationAssessment,
        local_refs: Mapping[str, str],
        graph: CaseGraph,
    ) -> ViolationAssessment:
        values = assessment.model_dump()
        for field_name in ("supporting_node_ids", "mitigating_node_ids"):
            values[field_name] = [cls._resolve_node_id(identifier, local_refs, graph) for identifier in values[field_name]]
        return ViolationAssessment.model_validate(values)
