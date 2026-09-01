import hashlib
import json
from pathlib import Path

from pydantic import TypeAdapter

from experiments.contract_assurance.inventory import inventory
from experiments.contract_assurance.registry import StewardDecisionResponse
from experiments.steward_screen.prompt import build_prompt
from experiments.steward_screen.scenarios import all_scenarios
from experiments.steward_screen.sequential_live import frozen_manifest
from investigator.roles import StewardDecision
from investigator.schema_fingerprint import canonical_schema_json, schema_fingerprint


def test_authoritative_steward_schema_is_the_production_adapter() -> None:
    authoritative = TypeAdapter(StewardDecision).json_schema()
    assert authoritative == StewardDecisionResponse.model_json_schema()
    assert schema_fingerprint(authoritative) == "eae0fc818457116d4105b57bca11f45361d2f0c7a8ac86445a4dfa6595724d12"


def test_historical_and_live_fingerprint_methods_are_reproducible() -> None:
    metadata = StewardDecision.__metadata__
    historical = hashlib.sha256(json.dumps(metadata, sort_keys=True, default=str).encode()).hexdigest()
    old_live = hashlib.sha256(json.dumps(str(metadata), sort_keys=True, default=str).encode()).hexdigest()
    manifest = frozen_manifest()
    assert historical == "d51e77d4478ddd8d8d67517a3844776acc2809fcb92c99d5d428f419f74b4eae"
    assert old_live == "6cd9f45d2e00bc6a7dc30793e16c4081e723b00719c9448813e23732c2af09c0"
    assert manifest["historical_schema_hash"] == historical
    assert manifest["schema_hash"] == schema_fingerprint(TypeAdapter(StewardDecision).json_schema())


def test_schema_fingerprint_is_order_independent_and_shared_by_inventory() -> None:
    first = {"b": {"z": 1, "a": 2}, "a": [3, 2, 1]}
    second = {"a": [3, 2, 1], "b": {"a": 2, "z": 1}}
    assert canonical_schema_json(first) == canonical_schema_json(second)
    assert schema_fingerprint(first) == schema_fingerprint(second)
    steward_entry = next(item for item in inventory(Path("."))["contracts"] if item["name"] == "StewardDecisionResponse")
    assert steward_entry["schema_hash"] == schema_fingerprint(TypeAdapter(StewardDecision).json_schema())


def test_steward_prompt_contains_all_authoritative_operation_branches_and_fields() -> None:
    schema = TypeAdapter(StewardDecision).json_schema()
    prompt = build_prompt(all_scenarios()[0])
    for branch in schema["$defs"].values():
        if "operation" not in branch.get("properties", {}):
            continue
        operation = branch["properties"]["operation"]["const"]
        assert f'"operation": "{operation}"' in prompt
        for field in branch["required"]:
            assert f'"{field}"' in prompt
