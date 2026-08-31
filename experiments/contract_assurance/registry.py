from dataclasses import dataclass
from typing import Any

from investigator.services import contracts
from experiments.model_screen.schemas import HypothesisResponse
from scripts.smoke_bedrock import SmokeResponse


@dataclass(frozen=True)
class ContractSpec:
    name: str
    schema: type[Any]
    source: str
    prompt_builders: tuple[str, ...]
    production_path: str
    template_placeholders: tuple[str, ...] = ()
    public: bool = True


CONTRACTS = (
    ContractSpec("SmokeResponse", SmokeResponse, "scripts/smoke_bedrock.py", ("scripts.smoke_bedrock.main",), "BedrockModelClient.call (manual smoke script; excluded from offline runs)"),
    ContractSpec("ModelScreenHypothesisResponse", HypothesisResponse, "experiments/model_screen/schemas.py", ("experiments.model_screen.prompt",), "ExperimentRunner.run -> ModelClient.call"),
    ContractSpec("InitialResponse", contracts.InitialResponse, "src/investigator/services/contracts.py", ("Case1ControlledEnvironment.initial_prompt",), "InvestigationService.start_case -> environment.build_initial_state"),
    ContractSpec("InitialExpansionResponse", contracts.InitialExpansionResponse, "src/investigator/services/contracts.py", ("Case1ControlledEnvironment.initial_expansion_prompt",), "InvestigationService.start_case(seed) -> environment.build_seeded_initial_state"),
    ContractSpec("NextActionResponse", contracts.NextActionResponse, "src/investigator/services/contracts.py", ("Case1ControlledEnvironment.next_action_prompt",), "InvestigationService.propose_next_action -> availability preflight"),
    ContractSpec("RevisionResponse", contracts.RevisionResponse, "src/investigator/services/contracts.py", ("Case1ControlledEnvironment.revision_prompt",), "InvestigationService.propose_revision -> apply_revision", ("REPLACE_WITH_",)),
    ContractSpec("NextStepResponse", contracts.NextStepResponse, "src/investigator/services/contracts.py", (), "Defined LLM-facing union; no current production caller"),
)


def contract_registry() -> dict[str, ContractSpec]:
    names = [item.name for item in CONTRACTS]
    if len(names) != len(set(names)):
        raise ValueError("Duplicate contract names in assurance registry")
    return {item.name: item for item in CONTRACTS}


def validate_registry() -> None:
    specs = list(contract_registry().values())
    schema_names = [item.schema.__name__ for item in specs]
    if len(schema_names) != len(set(schema_names)):
        raise ValueError("Duplicate schemas in assurance registry")
