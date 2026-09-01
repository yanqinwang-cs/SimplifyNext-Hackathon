from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from investigator.llm.base import ModelCallMetadata
from investigator.models.hypothesis import HypothesisStatus, HypothesisTransition, UncertaintyTransition
from investigator.models.identifiers import Case1ActionId, EvidenceId, HypothesisId, UncertaintyId
from investigator.state.case_state import CaseState


def _reject_placeholder(value: str | None) -> str | None:
    if value is None:
        return value
    normalized = value.strip()
    if not normalized:
        raise ValueError("Substantive text cannot be empty")
    if normalized.startswith("REPLACE_WITH_") or normalized in {
        "The uncertainty this enquiry addresses.",
        "How the result could change the explanation space.",
        "Why this enquiry is useful now.",
        "How the evidence changed the state.",
    }:
        raise ValueError("Template placeholder is not valid model output")
    return value


def _reject_empty_items(values: list[str]) -> list[str]:
    if any(not value.strip() for value in values):
        raise ValueError("Substantive text cannot be empty")
    return values


class HypothesisProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: HypothesisId
    parent_id: HypothesisId | None = None
    statement: str
    status: HypothesisStatus = HypothesisStatus.ACTIVE
    supported_by: list[EvidenceId]
    conflicted_by: list[EvidenceId]
    unresolved: list[str] = Field(min_length=1)
    specificity_basis_evidence_ids: list[EvidenceId]

    _valid_text = field_validator("statement")(_reject_placeholder)
    _valid_unresolved = field_validator("unresolved")(_reject_empty_items)


class InitialHypothesisProposal(HypothesisProposal):
    """LLM-facing initial hypothesis: only viable active hypotheses are emitted."""

    status: Literal[HypothesisStatus.ACTIVE]


class InitialResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hypotheses: list[InitialHypothesisProposal] = Field(min_length=1)
    selected_action_id: Case1ActionId
    target_uncertainty: str
    expected_information_value: str
    why_this_action_now: str

    _valid_text = field_validator("target_uncertainty", "expected_information_value", "why_this_action_now")(_reject_placeholder)

    @model_validator(mode="before")
    @classmethod
    def coerce_active_proposals(cls, values: Any) -> Any:
        if isinstance(values, dict) and isinstance(values.get("hypotheses"), list):
            values = dict(values)
            values["hypotheses"] = [
                item.model_dump(mode="python") if isinstance(item, HypothesisProposal) else item
                for item in values["hypotheses"]
            ]
        return values


class SeedHypothesisAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supported_by: list[EvidenceId]
    conflicted_by: list[EvidenceId]
    unresolved: list[str] = Field(min_length=1)
    specificity_basis_evidence_ids: list[EvidenceId]

    _valid_unresolved = field_validator("unresolved")(_reject_empty_items)


class InitialExpansionHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: HypothesisId
    parent_id: HypothesisId | None = None
    statement: str
    status: Literal[HypothesisStatus.ACTIVE]
    supported_by: list[EvidenceId]
    conflicted_by: list[EvidenceId]
    unresolved: list[str] = Field(min_length=1)
    specificity_basis_evidence_ids: list[EvidenceId]
    relationship: Literal["competing_root", "specialization"]
    contrasted_hypothesis_id: HypothesisId | None = None
    material_difference: str | None = None

    _valid_text = field_validator("statement", "material_difference")(_reject_placeholder)
    _valid_unresolved = field_validator("unresolved")(_reject_empty_items)

    @model_validator(mode="after")
    def validate_relationship(self) -> "InitialExpansionHypothesis":
        if self.parent_id == self.id:
            raise ValueError("A hypothesis cannot be its own parent")
        if self.relationship == "competing_root":
            if self.parent_id is not None or self.contrasted_hypothesis_id is None or not self.material_difference:
                raise ValueError("competing_root requires null parent, contrast target, and material_difference")
            if self.specificity_basis_evidence_ids:
                raise ValueError("competing_root cannot contain specialization evidence")
            if self.contrasted_hypothesis_id == self.id:
                raise ValueError("A competing root cannot contrast against itself")
        elif self.parent_id is None or not self.specificity_basis_evidence_ids:
            raise ValueError("specialization requires a parent and specificity-basis evidence")
        elif self.contrasted_hypothesis_id is not None or self.material_difference is not None:
            raise ValueError("specialization cannot contain competing-root fields")
        return self


class InitialExpansionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seed_analysis: SeedHypothesisAnalysis
    competing_hypotheses: list[InitialExpansionHypothesis] = Field(min_length=1)
    selected_action_id: Case1ActionId
    target_uncertainty: str
    expected_information_value: str
    why_this_action_now: str

    _valid_target = field_validator("target_uncertainty", "expected_information_value", "why_this_action_now")(_reject_placeholder)


class NextStepResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step_type: Literal["action", "conclusion", "stop_unresolved"]
    selected_action_id: Case1ActionId | None = None
    target_uncertainty: str | None = None
    expected_information_value: str | None = None
    why_this_action_now: str | None = None
    conclusion_hypothesis_id: HypothesisId | None = None
    conclusion_reason: str | None = None
    remaining_uncertainty_ids: list[UncertaintyId] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_step(self) -> "NextStepResponse":
        if self.step_type == "action":
            if self.selected_action_id is None or any(value is None for value in (self.target_uncertainty, self.expected_information_value, self.why_this_action_now)) or self.conclusion_hypothesis_id is not None or self.conclusion_reason is not None or self.remaining_uncertainty_ids:
                raise ValueError("action step requires action fields and no conclusion fields")
        elif self.step_type == "conclusion":
            if self.conclusion_hypothesis_id is None or not self.conclusion_reason or self.selected_action_id is not None or self.remaining_uncertainty_ids:
                raise ValueError("conclusion step requires hypothesis and reason and no action")
        elif self.selected_action_id is not None or self.target_uncertainty is not None or self.expected_information_value is not None or self.why_this_action_now is not None or self.conclusion_hypothesis_id is not None:
            raise ValueError("stop_unresolved cannot contain action or conclusion-target fields")
        elif not self.conclusion_reason and not self.remaining_uncertainty_ids:
            raise ValueError("stop_unresolved requires a reason or unresolved IDs")
        return self

    _valid_text = field_validator("target_uncertainty", "expected_information_value", "why_this_action_now", "conclusion_reason")(_reject_placeholder)


class NextActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selected_action_id: Case1ActionId
    target_uncertainty: str
    expected_information_value: str
    why_this_action_now: str

    _valid_text = field_validator("target_uncertainty", "expected_information_value", "why_this_action_now")(_reject_placeholder)


class NewUncertainty(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UncertaintyId
    hypothesis_id: HypothesisId
    description: str
    basis_evidence_ids: list[EvidenceId] = Field(default_factory=list)

    _valid_description = field_validator("description")(_reject_placeholder)

    @model_validator(mode="after")
    def validate_owner(self) -> "NewUncertainty":
        if self.id.split(":", 1)[0] != self.hypothesis_id:
            raise ValueError("New uncertainty ID must belong to its hypothesis_id")
        return self


class RevisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hypothesis_updates: list[HypothesisTransition] = Field(default_factory=list)
    new_hypotheses: list[HypothesisProposal] = Field(default_factory=list)
    uncertainty_updates: list[UncertaintyTransition] = Field(default_factory=list)
    new_uncertainties: list[NewUncertainty] = Field(default_factory=list)
    revision_rationale: str

    _valid_rationale = field_validator("revision_rationale")(_reject_placeholder)

    @model_validator(mode="after")
    def reject_duplicate_updates(self) -> "RevisionResponse":
        hypothesis_ids = [item.hypothesis_id for item in self.hypothesis_updates]
        uncertainty_ids = [item.uncertainty_id for item in self.uncertainty_updates]
        if len(hypothesis_ids) != len(set(hypothesis_ids)):
            raise ValueError("A hypothesis may have at most one transition per revision")
        if len(uncertainty_ids) != len(set(uncertainty_ids)):
            raise ValueError("An uncertainty may have at most one transition per revision")
        return self


class ReleaseRecord(BaseModel):
    action_id: Case1ActionId
    artifact_id: EvidenceId
    artifact_path: str
    source_type: str
    content: str


class ControlledRunTrace(BaseModel):
    model_id: str
    case_id: str = "case_01"
    initial_case_input: str
    initial_prompt: str
    initial_raw_model_output: Any | None = None
    initial_response: InitialResponse | None = None
    initial_hypothesis_state: CaseState | None = None
    initial_metadata: ModelCallMetadata | None = None
    selected_action_id: Case1ActionId | None = None
    target_uncertainty: str | None = None
    expected_information_value: str | None = None
    why_this_action_now: str | None = None
    release: ReleaseRecord | None = None
    revision_prompt: str | None = None
    revision_raw_model_output: Any | None = None
    revision_response: RevisionResponse | None = None
    revision_metadata: ModelCallMetadata | None = None
    final_hypothesis_state: CaseState | None = None
    parse_success: bool = False
    failure_stage: str | None = None
    error_message: str | None = None
    unsupported_operations: list[dict[str, Any]] = Field(default_factory=list)
