from time import perf_counter
from typing import Any

from pydantic import BaseModel

from investigator.llm.base import (
    MessageInput,
    ModelCallMetadata,
    ModelCallResult,
    ModelNativeCall,
    parse_model_output,
)


class MockModelClient:
    """Deterministic client for tests; it never performs network access."""

    def __init__(
        self,
        response: BaseModel | dict[str, Any],
        metadata: ModelCallMetadata | None = None,
    ) -> None:
        self.response = response
        self.metadata = metadata
        self.calls: list[tuple[MessageInput, type[BaseModel]]] = []

    def call(self, input_data: MessageInput, output_schema: type[BaseModel]) -> ModelCallResult:
        started = perf_counter()
        self.calls.append((input_data, output_schema))
        parsed = parse_model_output(self.response, output_schema)
        metadata = self.metadata or ModelCallMetadata(
            provider="mock",
            model="deterministic-mock",
            latency_seconds=perf_counter() - started,
            parse_success=True,
        )
        return ModelCallResult(parsed=parsed, metadata=metadata, raw_output=self.response)

    def call_native(self, input_data: MessageInput, tools: list[dict[str, Any]]) -> ModelNativeCall:
        if not isinstance(self.response, ModelNativeCall):
            raise TypeError("MockModelClient native calls require a ModelNativeCall")
        return self.response
