"""Deterministic, provenance-preserving mutations for offline contract checks."""

import copy
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Mutation:
    name: str
    intended_code: str
    raw_output: str


def mutations(value: dict[str, Any], *, required_fields: tuple[str, ...] = (), contract: str | None = None) -> list[Mutation]:
    result = [Mutation("empty", "S0", ""), Mutation("whitespace", "S0", "   "), Mutation("prose_only", "S0", "Here is the requested object.")]
    canonical = json.dumps(value, sort_keys=True)
    result.extend([
        Mutation("malformed_json", "S0", canonical[:-1]),
        Mutation("prose_before_json", "S0", "Here is the object:\n" + canonical),
        Mutation("trailing_material", "S0", canonical + "\nDone."),
        Mutation("fenced_json", "valid", "```json\n" + canonical + "\n```"),
    ])
    for field in required_fields:
        if field in value:
            mutated = copy.deepcopy(value)
            del mutated[field]
            result.append(Mutation(f"remove_{field}", "S1", json.dumps(mutated, sort_keys=True)))
            original = value[field]
            wrong = copy.deepcopy(value)
            if isinstance(original, list):
                wrong[field] = {}
            elif isinstance(original, dict):
                wrong[field] = []
            elif isinstance(original, str):
                wrong[field] = 7
            else:
                wrong[field] = "wrong primitive"
            result.append(Mutation(f"wrong_primitive_{field}", "S1", json.dumps(wrong, sort_keys=True)))
            if isinstance(original, list) and original:
                empty = copy.deepcopy(value)
                empty[field] = []
                result.append(Mutation(f"empty_required_{field}", "S1", json.dumps(empty, sort_keys=True)))
    extra = copy.deepcopy(value)
    extra["unexpected_field"] = "x"
    result.append(Mutation("unexpected_field", "S1", json.dumps(extra, sort_keys=True)))
    for field, field_value in value.items():
        if isinstance(field_value, list) and field_value and isinstance(field_value[0], dict):
            nested = copy.deepcopy(value)
            nested[field][0]["unexpected_nested_field"] = True
            result.append(Mutation(f"unexpected_nested_{field}", "S1", json.dumps(nested, sort_keys=True)))
    if "selected_action_id" in value:
        invented = copy.deepcopy(value)
        invented["selected_action_id"] = "inactive"
        result.append(Mutation("invented_literal", "S2", json.dumps(invented, sort_keys=True)))
        malformed = copy.deepcopy(value)
        malformed["selected_action_id"] = "A1 because it is useful"
        result.append(Mutation("id_with_explanation", "S2", json.dumps(malformed, sort_keys=True)))
        if contract == "NextActionResponse" and value["selected_action_id"] == "A1":
            unavailable = copy.deepcopy(value)
            unavailable["selected_action_id"] = "A2"
            result.append(Mutation("valid_but_unavailable_action", "S3", json.dumps(unavailable, sort_keys=True)))
    for field, field_value in value.items():
        if isinstance(field_value, list) and field_value and (field.endswith("_ids") or field.endswith("_by")):
            wrong_namespace = copy.deepcopy(value)
            wrong_namespace[field][0] = "H1" if field_value[0].startswith("E") else "E1"
            result.append(Mutation(f"wrong_namespace_{field}", "S2", json.dumps(wrong_namespace, sort_keys=True)))
    text_fields = [field for field, item in value.items() if isinstance(item, str)]
    if text_fields:
        placeholder = copy.deepcopy(value)
        placeholder[text_fields[0]] = "REPLACE_WITH_SUBSTANTIVE_TEXT"
        result.append(Mutation("placeholder_text", "S4", json.dumps(placeholder, sort_keys=True)))
    if "step_type" in value:
        polluted = copy.deepcopy(value)
        polluted["step_type"] = "action"
        polluted["conclusion_hypothesis_id"] = "H1"
        result.append(Mutation("mixed_union_branch", "S4", json.dumps(polluted, sort_keys=True)))
    if contract == "NextStepResponse":
        missing_stop_reason = {"step_type": "stop_unresolved", "remaining_uncertainty_ids": []}
        result.append(Mutation("stop_without_reason_or_ids", "S4", json.dumps(missing_stop_reason, sort_keys=True)))
        conclusion_with_action = {"step_type": "conclusion", "conclusion_hypothesis_id": "H1", "conclusion_reason": "A reason.", "selected_action_id": "A1"}
        result.append(Mutation("conclusion_with_action_field", "S4", json.dumps(conclusion_with_action, sort_keys=True)))
    if contract == "InitialExpansionResponse" and "competing_hypotheses" in value:
        mismatch = copy.deepcopy(value)
        mismatch["competing_hypotheses"][0]["parent_id"] = "H1"
        result.append(Mutation("competing_root_with_parent", "S4", json.dumps(mismatch, sort_keys=True)))
    if contract == "RevisionResponse":
        mismatch = copy.deepcopy(value)
        mismatch["new_uncertainties"] = [{"id": "H1:U1", "hypothesis_id": "H2", "description": "A new uncertainty."}]
        result.append(Mutation("uncertainty_owner_mismatch", "S4", json.dumps(mismatch, sort_keys=True)))
    if contract in {"InitialResponse", "InitialExpansionResponse"} and "hypotheses" in value:
        empty = copy.deepcopy(value)
        empty["hypotheses"] = []
        result.append(Mutation("empty_hypotheses", "S1", json.dumps(empty, sort_keys=True)))
    if contract == "InitialExpansionResponse":
        empty = copy.deepcopy(value)
        empty["competing_hypotheses"] = []
        result.append(Mutation("empty_competing_hypotheses", "S1", json.dumps(empty, sort_keys=True)))
    if contract in {"InitialResponse", "InitialExpansionResponse"}:
        unknown = copy.deepcopy(value)
        target = unknown["hypotheses"][0] if "hypotheses" in unknown else unknown["competing_hypotheses"][0]
        target["supported_by"] = ["E999"]
        result.append(Mutation("unknown_evidence_reference", "S3", json.dumps(unknown, sort_keys=True)))
    return result


def deduplicate(items: list[Mutation]) -> list[Mutation]:
    seen: set[str] = set()
    unique: list[Mutation] = []
    for item in items:
        if item.raw_output not in seen:
            seen.add(item.raw_output)
            unique.append(item)
    return unique


def write_fixture_manifest(destination, contract: str, canonical: dict[str, Any], required_fields: tuple[str, ...] = ()):
    """Write a small reproducible manifest; raw outputs are generated at evaluation time."""
    import json
    from pathlib import Path

    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"contract": contract, "canonical": canonical, "mutations": [item.__dict__ for item in deduplicate(mutations(canonical, required_fields=required_fields, contract=contract))]}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
