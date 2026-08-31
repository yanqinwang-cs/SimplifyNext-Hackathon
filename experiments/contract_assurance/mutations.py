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


def mutations(value: dict[str, Any], *, required_fields: tuple[str, ...] = ()) -> list[Mutation]:
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
    extra = copy.deepcopy(value)
    extra["unexpected_field"] = "x"
    result.append(Mutation("unexpected_field", "S1", json.dumps(extra, sort_keys=True)))
    if "selected_action_id" in value:
        invented = copy.deepcopy(value)
        invented["selected_action_id"] = "inactive"
        result.append(Mutation("invented_literal", "S2", json.dumps(invented, sort_keys=True)))
        malformed = copy.deepcopy(value)
        malformed["selected_action_id"] = "A1 because it is useful"
        result.append(Mutation("id_with_explanation", "S2", json.dumps(malformed, sort_keys=True)))
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
    payload = {"contract": contract, "canonical": canonical, "mutations": [item.__dict__ for item in deduplicate(mutations(canonical, required_fields=required_fields))]}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
