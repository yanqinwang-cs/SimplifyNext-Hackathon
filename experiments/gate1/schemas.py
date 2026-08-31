from typing import Any

from pydantic import BaseModel, Field

from investigator.llm.base import ModelCallMetadata


class ExperimentInput(BaseModel):
    case_id: str
    turn_number: int
    current_case_state: Any = None
    new_evidence: Any = None


class RunMetadata(BaseModel):
    case_id: str
    turn_number: int


class ExperimentResult(BaseModel):
    run: RunMetadata
    structured_result: Any
    call_metadata: ModelCallMetadata

