from enum import Enum
from typing import Annotated, Literal, TypeAlias, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StewardOperation(str, Enum):
    KEEP_FOCUS = "keep_focus"
    SHIFT_FOCUS = "shift_focus"
    GENERALIZE = "generalize"
    ARCHIVE = "archive"
    REACTIVATE = "reactivate"
    STOP_UNRESOLVED = "stop_unresolved"
    REQUEST_INFORMATION = "request_information"
    REQUEST_OPEN = "request_open"
    REQUEST_EVIDENCE = "request_evidence"


class StewardReviewContext(BaseModel):
    """Structural stop-review facts; it contains no truth or confidence score."""

    model_config = ConfigDict(extra="forbid")
    global_frontier_assessed: bool
    local_frontier_exhausted: bool
    local_exhaustion_required: bool = True
    available_action_ids: list[str] = Field(default_factory=list)
    materially_usable_action_ids: list[str] = Field(default_factory=list)
    neglected_candidate_node_ids: list[str] = Field(default_factory=list)
    active_unresolved_ids: list[str] = Field(default_factory=list)
    obvious_useful_region_remains: bool = False

    @model_validator(mode="after")
    def validate_actions(self) -> "StewardReviewContext":
        if not set(self.materially_usable_action_ids) <= set(self.available_action_ids):
            raise ValueError("materially usable actions must be available actions")
        return self


class _DecisionBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assessment: str
    reason: str


class KeepFocusDecision(_DecisionBase):
    operation: Literal["keep_focus"] = "keep_focus"


class ShiftFocusDecision(_DecisionBase):
    operation: Literal["shift_focus"] = "shift_focus"
    destination_node_id: str


class GeneralizeDecision(_DecisionBase):
    operation: Literal["generalize"] = "generalize"
    target_node_id: str


class ArchiveDecision(_DecisionBase):
    operation: Literal["archive"] = "archive"
    target_node_id: str
    destination_node_id: str | None = None


class ReactivateDecision(_DecisionBase):
    operation: Literal["reactivate"] = "reactivate"
    target_node_id: str


class StopUnresolvedDecision(_DecisionBase):
    operation: Literal["stop_unresolved"] = "stop_unresolved"
    important_unresolved_ids: list[str] = Field(min_length=1)
    reopening_conditions: str


class StewardRequestOpenDecision(_DecisionBase):
    operation: Literal["request_open"] = "request_open"
    information_sought: str
    expected_information_value: str


class StewardRequestEvidenceDecision(_DecisionBase):
    operation: Literal["request_evidence"] = "request_evidence"
    target_uncertainty_id: str
    information_sought: str
    expected_information_value: str


class StewardRequestInformationDecision(BaseModel):
    """Common production request shape; legacy request branches remain load-compatible."""

    model_config = ConfigDict(extra="forbid")
    operation: Literal["request_information"] = "request_information"
    assessment: str
    question: str
    reason: str | None = None
    target_uncertainty_id: str | None = None
    expected_information_value: str | None = None


StewardDecision: TypeAlias = Annotated[
    KeepFocusDecision | ShiftFocusDecision | GeneralizeDecision | ArchiveDecision | ReactivateDecision | StopUnresolvedDecision | StewardRequestOpenDecision | StewardRequestEvidenceDecision,
    Field(discriminator="operation"),
]

# Production may use the common request contract without changing the frozen
# StewardDecision schema consumed by experiment assurance snapshots.
ProductionStewardDecision: TypeAlias = Annotated[
    KeepFocusDecision | ShiftFocusDecision | GeneralizeDecision | ArchiveDecision | ReactivateDecision | StopUnresolvedDecision | StewardRequestInformationDecision | StewardRequestOpenDecision | StewardRequestEvidenceDecision,
    Field(discriminator="operation"),
]


def production_steward_operation_values(*, include_legacy: bool = True) -> tuple[str, ...]:
    """Return operation tags from the live production tagged union."""
    values: list[str] = []
    for branch in get_args(get_args(ProductionStewardDecision)[0]):
        values.extend(get_args(branch.model_fields["operation"].annotation))
    if not include_legacy:
        values = [value for value in values if value not in {"request_open", "request_evidence"}]
    return tuple(values)


def render_production_steward_contract() -> str:
    """Render a compact machine contract directly from production models."""
    branches = get_args(get_args(ProductionStewardDecision)[0])
    current = production_steward_operation_values(include_legacy=False)
    lines = [
        'TOP-LEVEL DISCRIMINATOR FIELD: exactly "operation".',
        'DO NOT USE "decision", "action", or "choice" as the output field.',
        "VALID CURRENT OPERATIONS: " + ", ".join(f'"{value}"' for value in current) + ".",
        'LEGACY COMPATIBILITY ONLY: "request_open", "request_evidence". Do not prefer these for new output.',
        "Required fields by operation (all fields not listed as optional are required):",
    ]
    for branch in branches:
        operation = get_args(branch.model_fields["operation"].annotation)[0]
        if operation in {"request_open", "request_evidence"}:
            continue
        required = [name for name, field in branch.model_fields.items() if name != "operation" and field.is_required()]
        optional = [name for name, field in branch.model_fields.items() if name != "operation" and not field.is_required()]
        line = f'- "{operation}": required [{", ".join(required)}]'
        if optional:
            line += f'; optional [{", ".join(optional)}]'
        lines.append(line)
    lines.extend([
        "Example valid stop_unresolved response:",
        '{"operation":"stop_unresolved","assessment":"The useful investigative frontier has been exhausted.","reason":"No materially useful enquiry remains based on the current case state.","important_unresolved_ids":["<valid unresolved id>"],"reopening_conditions":"New material evidence or corrected case information."}',
        'A response such as {"decision":"stop_unresolved",...} is INVALID.',
    ])
    return "\n".join(lines)
