from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StewardOperation(str, Enum):
    KEEP_FOCUS = "keep_focus"
    SHIFT_FOCUS = "shift_focus"
    GENERALIZE = "generalize"
    ARCHIVE = "archive"
    REACTIVATE = "reactivate"
    STOP_UNRESOLVED = "stop_unresolved"


class StewardDecision(BaseModel):
    """Global case-management proposal; it contains no tool/action authority."""

    model_config = ConfigDict(extra="forbid")
    assessment: str
    operation: StewardOperation
    target_node_id: str | None = None
    destination_node_id: str | None = None
    reason: str
    important_unresolved_ids: list[str] = Field(default_factory=list)
    supporting_graph_features: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_operation(self) -> "StewardDecision":
        if self.operation is StewardOperation.SHIFT_FOCUS and self.destination_node_id is None:
            raise ValueError("SHIFT_FOCUS requires destination_node_id")
        if self.operation in {StewardOperation.GENERALIZE, StewardOperation.ARCHIVE, StewardOperation.REACTIVATE} and self.target_node_id is None:
            raise ValueError(f"{self.operation.value} requires target_node_id")
        if self.operation is StewardOperation.STOP_UNRESOLVED and not self.important_unresolved_ids:
            raise ValueError("STOP_UNRESOLVED requires important unresolved IDs")
        return self
