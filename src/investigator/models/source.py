from enum import Enum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class SourceType(str, Enum):
    PERSON = "person"
    DOCUMENT = "document"
    SYSTEM = "system"
    DATABASE = "database"
    DEVICE = "device"
    OTHER = "other"


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    source_type: SourceType
    content: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
