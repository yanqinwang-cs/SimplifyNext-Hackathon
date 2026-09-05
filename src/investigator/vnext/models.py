"""Structural contracts for the finite vNext assessment path.

These models contain no control-flow, interviewing, or Steward concepts.
"""

from enum import Enum
from typing import TYPE_CHECKING, Annotated, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from investigator.models.source import Source
from investigator.models.assessment import AssessmentContext, AssessmentSubject, SubjectRelationship, validate_identity_references, validate_vnext_relationship_provenance

if TYPE_CHECKING:
    from investigator.state.case_state import CaseState

from investigator.roles.investigator import (
    AddConflictCommand,
    AddDerivationCommand,
    AddEvidenceCommand,
    AddHypothesisCommand,
    AddPropositionCommand,
    AddSpecializationCommand,
    AddSupportCommand,
    AddUncertaintyCommand,
)


InvestigatorProposalUpdate: TypeAlias = Annotated[
    AddEvidenceCommand
    | AddPropositionCommand
    | AddHypothesisCommand
    | AddUncertaintyCommand
    | AddSupportCommand
    | AddConflictCommand
    | AddDerivationCommand
    | AddSpecializationCommand,
    Field(discriminator="operation"),
]


class InvestigatorProposal(BaseModel):
    """A graph-operation proposal, without control flow or graph mutation."""

    model_config = ConfigDict(extra="forbid")
    graph_updates: list[InvestigatorProposalUpdate] = Field(default_factory=list)


class AssessmentStatus(str, Enum):
    """A bounded evidential assessment, not an institutional finding.

    NOT_CURRENTLY_SUPPORTED means the present record does not support the
    violation; it does not mean innocence or exoneration was established.
    """

    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    CONFLICTED = "conflicted"
    NOT_CURRENTLY_SUPPORTED = "not_currently_supported"


class Confidence(str, Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    VERY_HIGH = "very_high"


class ViolationDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    violation_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    rule_text: str = Field(min_length=1)
    prohibited_conduct: str = Field(min_length=1)


class AssessmentRulePreset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    preset_id: str = Field(min_length=1)
    violations: list[ViolationDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def violation_ids_are_unique(self) -> "AssessmentRulePreset":
        identifiers = [item.violation_id for item in self.violations]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("violation_id values must be unique within an assessment preset")
        return self


class VNextRunInput(BaseModel):
    """Persistent case inputs for one clean, finite vNext run."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    case_context: str | None = None
    sources: dict[str, Source] = Field(default_factory=dict)
    assessment_context: AssessmentContext | None = None
    subjects: dict[str, AssessmentSubject] = Field(default_factory=dict)
    subject_relationships: dict[str, SubjectRelationship] = Field(default_factory=dict)
    rule_preset: AssessmentRulePreset
    human_inputs: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def source_keys_match_ids(self) -> "VNextRunInput":
        if any(key != source.id for key, source in self.sources.items()):
            raise ValueError("Source mapping keys must match source IDs")
        validate_identity_references(self.subjects, self.subject_relationships, self.sources)
        return self

    @classmethod
    def from_case_state(
        cls,
        case_state: "CaseState",
        rule_preset: AssessmentRulePreset,
        *,
        human_inputs: dict[str, object] | None = None,
    ) -> "VNextRunInput":
        validate_vnext_relationship_provenance(case_state.subject_relationships)
        return cls(
            case_id=case_state.case_id,
            sources=case_state.sources,
            subjects=case_state.subjects,
            subject_relationships=case_state.subject_relationships,
            rule_preset=rule_preset,
            human_inputs=dict(human_inputs or {}),
        )


class ViolationAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    violation_id: str = Field(min_length=1)
    status: AssessmentStatus
    supporting_node_ids: list[str] = Field(default_factory=list)
    mitigating_node_ids: list[str] = Field(default_factory=list)
    unresolved_points: list[str] = Field(default_factory=list)
    reasoning_summary: str = Field(min_length=1)
    confidence: Confidence


class FurthestJustifiedConclusion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1)
    based_on_violation_ids: list[str] = Field(default_factory=list)
    confidence: Confidence


class SubjectAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: str = Field(min_length=1)
    violation_assessments: list[ViolationAssessment] = Field(min_length=1)
    furthest_conclusion: FurthestJustifiedConclusion


class InvestigatorAssessment(BaseModel):
    """One complete finite sweep partitioned by assessment subject."""

    model_config = ConfigDict(extra="forbid")

    proposal: InvestigatorProposal
    subject_assessments: list[SubjectAssessment] = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def adapt_legacy_single_subject_shape(cls, value: object) -> object:
        if isinstance(value, dict) and "subject_assessments" not in value and {"violation_assessments", "furthest_conclusion"} <= value.keys():
            legacy = dict(value)
            legacy["subject_assessments"] = [{
                "subject_id": "case_subject",
                "violation_assessments": legacy.pop("violation_assessments"),
                "furthest_conclusion": legacy.pop("furthest_conclusion"),
            }]
            return legacy
        return value

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
