from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class SourceType(str, Enum):
    PERSON = "person"
    DOCUMENT = "document"
    SYSTEM = "system"
    DATABASE = "database"
    DEVICE = "device"
    OTHER = "other"


class Source(BaseModel):
    id: str
    name: str
    source_type: SourceType
    metadata: dict[str, Any] = Field(default_factory=dict)

