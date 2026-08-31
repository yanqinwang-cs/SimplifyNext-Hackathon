from datetime import datetime
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class EvidenceKind(str, Enum):
    STATEMENT = "statement"
    RECORD = "record"
    COMMUNICATION = "communication"
    OBSERVATION = "observation"
    OTHER = "other"


class EvidenceItem(BaseModel):
    id: str = Field(frozen=True)
    source_id: str
    raw_content: str
    kind: EvidenceKind
    received_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
