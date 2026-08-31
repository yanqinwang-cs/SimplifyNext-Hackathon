from pydantic import BaseModel, Field


class Conflict(BaseModel):
    id: str
    description: str
    claim_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    resolved: bool = False
    resolution_note: str | None = None

