from dataclasses import dataclass, field
from typing import Any


REQUIRED_FIELDS = ("change_id", "contract", "discovery_source", "baseline_failure", "classification", "legitimate_semantic_need", "why_existing_contract_insufficient", "implementation_change", "tests_added", "expected_reclassifications", "unexpected_regressions", "before_after_compliance", "commit")


@dataclass(frozen=True)
class EvolutionRecord:
    change_id: str
    contract: str
    discovery_source: str
    baseline_failure: dict[str, Any]
    classification: str
    legitimate_semantic_need: str
    why_existing_contract_insufficient: str
    implementation_change: str
    tests_added: list[str] = field(default_factory=list)
    expected_reclassifications: list[str] = field(default_factory=list)
    unexpected_regressions: list[str] = field(default_factory=list)
    before_after_compliance: dict[str, Any] = field(default_factory=dict)
    commit: str = ""


def validate_evolution_record(record: dict[str, Any]) -> list[str]:
    return [field for field in REQUIRED_FIELDS if not record.get(field)]
