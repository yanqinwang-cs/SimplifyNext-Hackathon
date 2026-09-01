from dataclasses import dataclass, field
from typing import Any

from investigator.cycle import InvestigatorObservation


@dataclass(frozen=True)
class SemanticRequirement:
    kind: str
    basis: tuple[str, ...]
    values: tuple[str, ...] = ()


@dataclass
class InvestigatorFixture:
    fixture_id: str
    description: str
    observation: InvestigatorObservation
    required: list[SemanticRequirement] = field(default_factory=list)
    forbidden: list[SemanticRequirement] = field(default_factory=list)
    acceptable_next_steps: frozenset[str] = frozenset()
    public_basis: dict[str, tuple[str, ...]] = field(default_factory=dict)
    protected_node_ids: frozenset[str] = frozenset()
    expected_action_ids: frozenset[str] = frozenset()
    expected_target_uncertainty_ids: frozenset[str] = frozenset()


@dataclass
class InvestigatorScreenResult:
    fixture_id: str
    schema_valid: bool = False
    production_applied: bool = False
    semantic_pass: bool = False
    failure_categories: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    next_step_type: str | None = None
    update_operations: list[str] = field(default_factory=list)
    raw_output: Any | None = None
