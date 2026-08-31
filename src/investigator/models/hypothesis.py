from enum import Enum
from pydantic import BaseModel, ConfigDict, Field, model_validator
from investigator.models.identifiers import EvidenceId, HypothesisId, UncertaintyId


class HypothesisOrigin(str, Enum):
    HUMAN = "human"
    HUMAN_INPUT = "human_input"
    AGENT_VARIANT = "agent_variant"
    AGENT_SUGGESTION = "agent_suggestion"


class HypothesisStatus(str, Enum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    DEPRIORITIZED = "deprioritized"
    REJECTED = "rejected"
    ARCHIVED = "archived"
    WEAKENED = "weakened"
    CONFLICTED = "conflicted"
    REMOVED = "removed"


class TransformationType(str, Enum):
    NEGATION = "negation"
    NARROWING = "narrowing"
    BROADENING = "broadening"
    SUBJECT_SUBSTITUTION = "subject_substitution"
    OBJECT_SUBSTITUTION = "object_substitution"
    DECOMPOSITION = "decomposition"


class Hypothesis(BaseModel):
    id: str = Field(frozen=True)
    statement: str
    origin: HypothesisOrigin
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    parent_hypothesis_id: str | None = None
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    conflicting_evidence_ids: list[str] = Field(default_factory=list)
    unresolved_issue_ids: list[str] = Field(default_factory=list)
    specificity_basis: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def accept_tree_parent_name(cls, values):
        if isinstance(values, dict) and "parent_id" in values and "parent_hypothesis_id" not in values:
            values = dict(values)
            values["parent_hypothesis_id"] = values.pop("parent_id")
        return values

    @property
    def parent_id(self) -> str | None:
        return self.parent_hypothesis_id


class HypothesisTransformation(BaseModel):
    parent_hypothesis_id: str
    child_hypothesis_id: str
    transformation_type: TransformationType


class HypothesisTransitionType(str, Enum):
    KEEP = "keep"
    WEAKEN = "weaken"
    CONFLICT = "conflict"
    REMOVE = "remove"
    ACTIVATE = "activate"
    OTHER = "other"


class HypothesisTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis_id: HypothesisId
    transition: HypothesisTransitionType
    reason: str
    add_supporting_evidence_ids: list[EvidenceId] = Field(default_factory=list)
    add_conflicting_evidence_ids: list[EvidenceId] = Field(default_factory=list)
    add_specificity_basis_evidence_ids: list[EvidenceId] = Field(default_factory=list)
    requested_operation_name: str | None = None
    requested_effect: str | None = None
    why_existing_operations_do_not_fit: dict[str, str] | None = None

    @model_validator(mode="after")
    def validate_other_fields(self) -> "HypothesisTransition":
        other_fields = (self.requested_operation_name, self.requested_effect, self.why_existing_operations_do_not_fit)
        if self.transition is HypothesisTransitionType.OTHER:
            if any(value is None for value in other_fields):
                raise ValueError("Hypothesis transition 'other' requires requested operation details")
        elif any(value is not None for value in other_fields):
            raise ValueError("OTHER-only hypothesis fields require transition='other'")
        return self


class UncertaintyTransitionType(str, Enum):
    KEEP = "keep"
    REFINE = "refine"
    RESOLVE = "resolve"
    REMOVE = "remove"
    OTHER = "other"


class UncertaintyTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uncertainty_id: UncertaintyId
    transition: UncertaintyTransitionType
    reason: str
    new_description: str | None = None
    basis_evidence_ids: list[EvidenceId] = Field(default_factory=list)
    requested_operation_name: str | None = None
    requested_effect: str | None = None
    why_existing_operations_do_not_fit: dict[str, str] | None = None

    @model_validator(mode="after")
    def validate_transition_fields(self) -> "UncertaintyTransition":
        other_fields = (self.requested_operation_name, self.requested_effect, self.why_existing_operations_do_not_fit)
        if self.transition is UncertaintyTransitionType.REFINE and self.new_description is None:
            raise ValueError("Uncertainty transition 'refine' requires new_description")
        if self.transition is not UncertaintyTransitionType.REFINE and self.new_description is not None:
            raise ValueError("new_description is only valid for uncertainty transition='refine'")
        if self.transition is UncertaintyTransitionType.OTHER:
            if any(value is None for value in other_fields):
                raise ValueError("Uncertainty transition 'other' requires requested operation details")
        elif any(value is not None for value in other_fields):
            raise ValueError("OTHER-only uncertainty fields require transition='other'")
        return self
