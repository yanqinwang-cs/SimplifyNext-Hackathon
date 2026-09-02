from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from investigator.graph import CaseGraph
from investigator.roles import InvestigationFocus, StewardReviewContext
from investigator.model_registry import MODEL_REGISTRY, ModelSpec


class ExpectedState(BaseModel):
    focus_node_id: str
    archived_node_ids: list[str] = Field(default_factory=list)
    active_node_ids: list[str] = Field(default_factory=list)
    stopped: bool = False


class StewardScenario(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")
    scenario_id: str
    description: str
    graph: CaseGraph
    focus: InvestigationFocus
    review_context: StewardReviewContext | None = None
    expected_operation: str
    expected_target_node_id: str | None = None
    expected_destination_node_id: str | None = None
    expected_state: ExpectedState
    forbidden_behavior_notes: list[str] = Field(default_factory=list)


class ScreenResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")
    run_id: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    model_name: str
    invocation_id: str
    scenario_id: str
    repetition: int
    prompt_hash: str
    raw_model_output: Any | None = None
    schema_valid: bool = False
    parsed_decision: dict[str, Any] | None = None
    expected_operation: str
    actual_operation: str | None = None
    expected_target_node_id: str | None = None
    actual_target_node_id: str | None = None
    expected_destination_node_id: str | None = None
    actual_destination_node_id: str | None = None
    operation_correct: bool = False
    identifier_correct: bool = False
    coordinator_accepted: bool = False
    post_state_correct: bool = False
    invented_identifier: bool = False
    tool_action_mention: bool = False
    input_tokens: int | None = None
    output_tokens: int | None = None
    elapsed_seconds: float | None = None
    retry_count: int = 0
    error_category: str | None = None
    error_message: str | None = None


class RunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    by_model: dict[str, dict[str, int]]
    by_scenario: dict[str, dict[str, int]]
