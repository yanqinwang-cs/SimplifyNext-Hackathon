from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EvidenceRequestStatus(str, Enum):
    PENDING = "pending"
    FULFILLED = "fulfilled"
    UNAVAILABLE = "unavailable"


class EvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(pattern=r"^R\d+$")
    reason: str
    target_uncertainty_id: str | None = None
    information_sought: str | None = None
    expected_information_value: str | None = None
    status: EvidenceRequestStatus = EvidenceRequestStatus.PENDING
    requested_at_revision: int = Field(ge=0)
    released_source_ids: list[str] = Field(default_factory=list)
    note: str | None = None
    originating_run_id: str | None = None
    originating_actor: str | None = None
    created_case_revision: int | None = Field(default=None, ge=0)
    fulfilled_case_revision: int | None = Field(default=None, ge=0)
    resumed_run_id: str | None = None


class EvidenceRequestSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(pattern=r"^R\d+$")
    status: Literal["fulfilled", "unavailable"]
    released_source_ids: list[str] = Field(default_factory=list)
    note: str | None = None


# Compatibility name for callers; this human-facing contract is not an
# LLM-generated response and therefore is intentionally not in the LLM inventory.
EvidenceRequestResponse = EvidenceRequestSubmission
