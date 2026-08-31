from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from investigator.llm.base import ModelCallMetadata


class Hypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str
    justification: str
    uncertainty: str


class HypothesisResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hypotheses: list[Hypothesis] = Field(min_length=2, max_length=4)

    @field_validator("hypotheses")
    @classmethod
    def require_two_to_four(cls, value: list[Hypothesis]) -> list[Hypothesis]:
        if not 2 <= len(value) <= 4:
            raise ValueError("expected between 2 and 4 hypotheses")
        return value


class CaseResult(BaseModel):
    case_id: str
    model_id: str
    case_input: str
    prompt: str
    parsed_output: HypothesisResponse | None = None
    raw_model_output: Any | None = None
    metadata: ModelCallMetadata | None = None
    parse_success: bool
    error_message: str | None = None


class RunSummary(BaseModel):
    model_id: str
    result_directory: str
    case_ids: list[str]
    successful_cases: int
    failed_cases: int
