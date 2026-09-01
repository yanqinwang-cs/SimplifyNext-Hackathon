from dataclasses import dataclass
from typing import Any
from pydantic import TypeAdapter

from investigator.services import contracts
from experiments.model_screen.schemas import HypothesisResponse
from scripts.smoke_bedrock import SmokeResponse
from investigator.roles import InvestigatorUpdateResponse, StewardDecision
from investigator.cycle import InvestigatorTurnResponse


class StewardDecisionResponse:
    """Assurance facade backed by the production StewardDecision TypeAdapter."""

    model_fields: dict[str, Any] = {}
    model_config = {"extra": "forbid"}
    _adapter = TypeAdapter(StewardDecision)

    @classmethod
    def model_json_schema(cls) -> dict[str, Any]:
        return cls._adapter.json_schema()

    @classmethod
    def model_validate(cls, value: Any) -> Any:
        return cls._adapter.validate_python(value)


@dataclass(frozen=True)
class ContractSpec:
    name: str
    schema: type[Any]
    source: str
    prompt_builders: tuple[str, ...]
    production_path: str
    template_placeholders: tuple[str, ...] = ()
    public: bool = True
    prompt_sources: tuple[str, ...] = ()
    boundary_stages: tuple[str, ...] = ()
    parser_entry_point: str = "investigator.llm.base.parse_model_output"
    normalization_behavior: str = "normalize_json_text; one outer JSON fence only"
    schema_validation: str = "Pydantic model validation"
    field_namespace_validation: str = "Pydantic field, literal, and identifier validation"
    referential_validation: str = "Production adapter/state boundary when applicable"
    availability_validation: str = "Production environment boundary when applicable"
    cross_field_validation: str = "Pydantic model validators and production consumer"
    deterministic_consumer: str = "Registered production-path adapter"
    raw_output_preserved_on_failure: bool = True
    usage: str = "Registered LLM-facing contract"


CONTRACTS = (
    ContractSpec("SmokeResponse", SmokeResponse, "scripts/smoke_bedrock.py", ("scripts.smoke_bedrock.main",), "BedrockModelClient.call (manual smoke script; excluded from offline runs)", template_placeholders=("REPLACE_WITH_",), prompt_sources=("scripts/smoke_bedrock.py",), boundary_stages=("JSON normalization", "Pydantic schema validation", "raw output retained by caller")),
    ContractSpec("ModelScreenHypothesisResponse", HypothesisResponse, "experiments/model_screen/schemas.py", ("experiments.model_screen.prompt",), "ExperimentRunner.run -> ModelClient.call", template_placeholders=("REPLACE_WITH_",), prompt_sources=("experiments/model_screen/prompt.py",), boundary_stages=("JSON normalization", "Pydantic schema validation", "experiment consumer")),
    ContractSpec("InitialResponse", contracts.InitialResponse, "src/investigator/services/contracts.py", ("Case1ControlledEnvironment.initial_prompt",), "InvestigationService.start_case -> environment.build_initial_state", template_placeholders=("REPLACE_WITH_",), prompt_sources=("src/investigator/environments/case_01_prompts.py",), boundary_stages=("JSON normalization", "Pydantic schema validation", "evidence namespace validation", "state construction", "raw output retained by service")),
    ContractSpec("InitialExpansionResponse", contracts.InitialExpansionResponse, "src/investigator/services/contracts.py", ("Case1ControlledEnvironment.initial_expansion_prompt",), "InvestigationService.start_case(seed) -> environment.build_seeded_initial_state", template_placeholders=("REPLACE_WITH_",), prompt_sources=("src/investigator/environments/case_01_prompts.py",), boundary_stages=("JSON normalization", "Pydantic schema validation", "relationship validation", "evidence namespace validation", "seeded state construction", "raw output retained by service")),
    ContractSpec("NextActionResponse", contracts.NextActionResponse, "src/investigator/services/contracts.py", ("Case1ControlledEnvironment.next_action_prompt",), "InvestigationService.propose_next_action -> availability preflight", template_placeholders=("REPLACE_WITH_",), prompt_sources=("src/investigator/environments/case_01_prompts.py",), boundary_stages=("JSON normalization", "Pydantic schema validation", "action namespace validation", "environment availability validation", "raw output retained by service")),
    ContractSpec("RevisionResponse", contracts.RevisionResponse, "src/investigator/services/contracts.py", ("Case1ControlledEnvironment.revision_prompt",), "InvestigationService.propose_revision -> apply_revision", ("REPLACE_WITH_",), prompt_sources=("src/investigator/environments/case_01_prompts.py",), boundary_stages=("JSON normalization", "Pydantic schema validation", "reference validation", "cross-field validation", "state operation preflight", "raw output retained by service")),
    ContractSpec("NextStepResponse", contracts.NextStepResponse, "src/investigator/services/contracts.py", (), "Defined LLM-facing union; no current production caller", template_placeholders=("REPLACE_WITH_",), prompt_sources=(), boundary_stages=("JSON normalization", "Pydantic schema validation", "union branch validation", "raw output retained by adapter")),
    ContractSpec("StewardDecisionResponse", StewardDecisionResponse, "experiments/steward_screen/runner.py", ("experiments.steward_screen.prompt.build_prompt",), "steward_screen.runner.run_live -> GraphInvestigationCoordinator.review_with_steward", prompt_sources=("experiments/steward_screen/prompt.py",), boundary_stages=("JSON normalization", "provider JSON envelope", "StewardDecision union validation", "coordinator operation preflight", "raw output retained by screen result")),
    ContractSpec("InvestigatorUpdate", InvestigatorUpdateResponse, "src/investigator/roles/investigator.py", (), "GraphInvestigationCoordinator.apply_investigator_update", public=False, boundary_stages=("Pydantic discriminated-union validation", "locality/type preflight", "atomic graph mutation")),
    ContractSpec("InvestigatorTurnResponse", InvestigatorTurnResponse, "src/investigator/cycle.py", ("investigator.cycle_prompt.build_investigator_cycle_prompt",), "InvestigatorCycleCoordinator.apply_turn", public=False, prompt_sources=("src/investigator/cycle_prompt.py",), boundary_stages=("Pydantic turn-schema validation", "ordered graph-update preflight", "atomic cycle transaction")),
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
