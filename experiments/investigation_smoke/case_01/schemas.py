from typing import Any

from pydantic import BaseModel, Field, field_validator

from investigator.llm.base import ModelCallMetadata
from investigator.models.hypothesis import HypothesisTransition, HypothesisTransitionType
from investigator.state.case_state import CaseState


class HypothesisProposal(BaseModel):
    id: str
    parent_id: str | None = None
    statement: str
    status: str = "active"
    supported_by: list[str] = Field(default_factory=list)
    conflicted_by: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    specificity_basis: list[str] = Field(default_factory=list)


class InitialResponse(BaseModel):
    hypotheses: list[HypothesisProposal] = Field(min_length=1)
    selected_action_id: str
    target_uncertainty: str
    expected_information_value: str
    why_this_action_now: str


class RevisionResponse(BaseModel):
    hypothesis_updates: list[HypothesisTransition] = Field(default_factory=list)
    new_hypotheses: list[HypothesisProposal] = Field(default_factory=list)
    remaining_uncertainties: list[str] = Field(default_factory=list)
    revision_rationale: str


class ReleaseRecord(BaseModel):
    action_id: str
    artifact_id: str
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
    selected_action_id: str | None = None
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
    error_message: str | None = None

    @field_validator("selected_action_id")
    @classmethod
    def selected_action_is_single(cls, value: str | None) -> str | None:
        return value

