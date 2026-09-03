from collections.abc import Mapping, Sequence
import json
import re
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T", bound=BaseModel)
MessageInput = str | Sequence[Mapping[str, Any]]


def normalize_json_text(raw_text: str) -> str:
    """Normalize one Markdown JSON fence without repairing structured content."""
    text = raw_text.strip()
    first_line, separator, remainder = text.partition("\n")
    if first_line.strip().lower() in {"```", "```json"} and separator and remainder.endswith("```"):
        return remainder[:-3]
    blocks = list(re.finditer(r"```(?:json)?[ \t]*\r?\n(.*?)```", text, flags=re.IGNORECASE | re.DOTALL))
    if len(blocks) == 1:
        return blocks[0].group(1)
    return text


class ModelParseError(ValueError):
    """Raised when a model response cannot be validated against its schema."""

    def __init__(self, message: str, raw_output: Any = None) -> None:
        super().__init__(message)
        self.raw_output = raw_output


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
    raw_output: Any | None = None


class ModelTextBlock(BaseModel):
    text: str


class ModelToolUse(BaseModel):
    call_id: str
    name: str
    arguments: dict[str, Any]


class ModelNativeCall(BaseModel):
    """Provider-neutral conversational response after adapter translation."""
    text_blocks: list[ModelTextBlock] = Field(default_factory=list)
    tool_uses: list[ModelToolUse] = Field(default_factory=list)
    metadata: ModelCallMetadata
    raw_output: Any | None = None


class ModelClient(Protocol):
    def call(self, input_data: MessageInput, output_schema: type[T]) -> ModelCallResult:
        """Make one structured call and return its validated result."""

    def call_native(self, input_data: MessageInput, tools: list[dict[str, Any]]) -> ModelNativeCall:
        """Make one conversational call with provider-native tool use."""


def parse_model_output(raw_output: Any, output_schema: type[T]) -> T:
    try:
        if isinstance(raw_output, output_schema):
            return raw_output
        if isinstance(raw_output, str):
            raw_output = json.loads(normalize_json_text(raw_output))
        return output_schema.model_validate(raw_output)
    except Exception as exc:
        raise ModelParseError(
            f"Could not parse model output as {output_schema.__name__}: {exc}"
        ) from exc
