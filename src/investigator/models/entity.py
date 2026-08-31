from typing import Any
from pydantic import BaseModel, Field


class Entity(BaseModel):
    id: str
    entity_type: str
    display_name: str
    aliases: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

