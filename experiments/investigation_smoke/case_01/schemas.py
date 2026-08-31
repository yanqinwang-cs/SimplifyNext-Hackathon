from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from investigator.llm.base import ModelCallMetadata
from investigator.models.hypothesis import HypothesisStatus, HypothesisTransition
from investigator.state.case_state import CaseState


class HypothesisProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    parent_id: str | None = None
    statement: str
    status: HypothesisStatus = HypothesisStatus.ACTIVE
    supported_by: list[str]
    conflicted_by: list[str]
    unresolved: list[str] = Field(min_length=1)
    specificity_basis: list[str]


class InitialResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypotheses: list[HypothesisProposal] = Field(min_length=1)
    selected_action_id: str
    target_uncertainty: str
    expected_information_value: str
    why_this_action_now: str


class RevisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    failure_stage: str | None = None
    error_message: str | None = None
