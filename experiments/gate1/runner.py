from pydantic import BaseModel

from investigator.llm.base import ModelClient
from experiments.gate1.schemas import ExperimentInput, ExperimentResult, RunMetadata


class ExperimentRunner:
    def __init__(self, model_client: ModelClient) -> None:
        self.model_client = model_client

    def run(self, experiment_input: ExperimentInput, output_schema: type[BaseModel]) -> ExperimentResult:
        call = self.model_client.call(
            experiment_input.model_dump(mode="json"),
            output_schema,
        )
        return ExperimentResult(
            run=RunMetadata(case_id=experiment_input.case_id, turn_number=experiment_input.turn_number),
            structured_result=call.parsed,
            call_metadata=call.metadata,
        )

