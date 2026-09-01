import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CaseParticipant(BaseModel):
    """A contextual participant reference, not a CaseGraph epistemic node."""

    model_config = ConfigDict(extra="forbid")
    id: str
    participant_kind: Literal["person"] = "person"
    contextual_roles: list[str] = Field(min_length=1)
    display_label: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not re.fullmatch(r"PERSON[A-Z0-9_.-]+", value):
            raise ValueError("participant IDs must use the PERSON... namespace")
        return value

    @field_validator("contextual_roles")
    @classmethod
    def validate_roles(cls, value: list[str]) -> list[str]:
        if any(not role or role in {"offender", "cheater", "guilty_party"} for role in value):
            raise ValueError("participant roles must be contextual and non-conclusive")
        return value
