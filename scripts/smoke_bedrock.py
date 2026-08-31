"""Make one minimal live Bedrock structured-call smoke test."""

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, field_validator

from investigator.llm.bedrock import BedrockModelClient


class SmokeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str

    @field_validator("answer")
    @classmethod
    def reject_template_sentinel(cls, value: str) -> str:
        if value.strip().startswith("REPLACE_WITH_"):
            raise ValueError("Template placeholder is not valid model output")
        return value


def main() -> None:
    # load_dotenv does not override explicitly exported environment variables.
    load_dotenv()
    client = BedrockModelClient()
    result = client.call(
        'Return only JSON matching {"answer": "..."}. What is 2 + 2?',
        SmokeResponse,
    )
    metadata = result.metadata
    print(f"parsed answer: {result.parsed.answer}")
    print(f"provider: {metadata.provider}")
    print(f"model: {metadata.model}")
    print(f"input tokens: {metadata.input_tokens}")
    print(f"output tokens: {metadata.output_tokens}")
    print(f"latency seconds: {metadata.latency_seconds}")
    print(f"stop reason: {metadata.finish_reason}")


if __name__ == "__main__":
    main()
