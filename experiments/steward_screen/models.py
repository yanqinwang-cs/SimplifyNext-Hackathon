from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from investigator.graph import CaseGraph
from investigator.models import CaseParticipant
from investigator.roles import InvestigationFocus, StewardReviewContext


class ModelSpec(BaseModel):
    name: str
    invocation_id: str
    region: str = "us-east-1"


MODEL_REGISTRY = {
    name: ModelSpec(name=name, invocation_id=name)
    for name in (
        "zai.glm-5", "deepseek.v3.2", "moonshot.kimi-k2-thinking",
        "qwen.qwen3-next-80b-a3b", "openai.gpt-oss-120b-1:0",
        "amazon.nova-2-lite-v1:0", "zai.glm-4.7-flash",
    )
}


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
    participants: list[CaseParticipant] = Field(default_factory=lambda: [CaseParticipant(id="PERSON1", contextual_roles=["candidate", "student"], display_label="Candidate 1"), CaseParticipant(id="PERSON2", contextual_roles=["tutor", "staff_member"], display_label="Tutor or staff member")])
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
    schema_failure_code: str | None = None
    schema_recoverable: bool = False
    diagnostic_operation: str | None = None
    diagnostic_target_node_id: str | None = None
    diagnostic_destination_node_id: str | None = None
    diagnostic_operation_correct: bool | None = None
    diagnostic_identifier_correct: bool | None = None
    diagnostic_decision_correct: bool | None = None


class RunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    by_model: dict[str, dict[str, int]]
    by_scenario: dict[str, dict[str, int]]
