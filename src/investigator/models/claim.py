from enum import Enum
from pydantic import BaseModel, Field


class ClaimStatus(str, Enum):
    ASSERTED = "asserted"
    QUALIFIED = "qualified"
    CONTESTED = "contested"
    WITHDRAWN = "withdrawn"


class Claim(BaseModel):
    id: str
    proposition: str
    asserted_by_source_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    status: ClaimStatus = ClaimStatus.ASSERTED

