from collections.abc import Mapping, Sequence
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T", bound=BaseModel)
MessageInput = str | Sequence[Mapping[str, Any]]


class ModelParseError(ValueError):
    """Raised when a model response cannot be validated against its schema."""


class ModelCallMetadata(BaseModel):
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_seconds: float
    parse_success: bool
    finish_reason: str | None = None


class ModelCallResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    parsed: BaseModel
    metadata: ModelCallMetadata


class ModelClient(Protocol):
    def call(self, input_data: MessageInput, output_schema: type[T]) -> ModelCallResult:
        """Make one structured call and return its validated result."""


def parse_model_output(raw_output: Any, output_schema: type[T]) -> T:
    try:
        if isinstance(raw_output, output_schema):
            return raw_output
        return output_schema.model_validate(raw_output)
    except Exception as exc:
        raise ModelParseError(
            f"Could not parse model output as {output_schema.__name__}: {exc}"
        ) from exc

