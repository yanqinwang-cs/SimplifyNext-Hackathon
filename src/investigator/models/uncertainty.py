from enum import Enum
from pydantic import BaseModel, Field


class UncertaintyKind(str, Enum):
    UNKNOWN = "unknown"
    APPROXIMATE = "approximate"
    AMBIGUOUS = "ambiguous"
    CONTESTED = "contested"
    UNRESOLVED_IDENTITY = "unresolved_identity"


class Uncertainty(BaseModel):
    id: str
    kind: UncertaintyKind
    description: str
    evidence_ids: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)

