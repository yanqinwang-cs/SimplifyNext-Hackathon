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
            if original is not None:
                null_value = copy.deepcopy(value)
                null_value[field] = None
                result.append(Mutation(f"null_required_{field}", "S1", json.dumps(null_value, sort_keys=True)))
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
            nested_type = copy.deepcopy(value)
            nested_name, nested_value = next(iter(nested_type[field][0].items()))
            nested_type[field][0][nested_name] = [] if isinstance(nested_value, str) else "wrong nested primitive"
            result.append(Mutation(f"wrong_nested_primitive_{field}_{nested_name}", "S1", json.dumps(nested_type, sort_keys=True)))
            if nested_value is not None:
                nested_null = copy.deepcopy(value)
                nested_null[field][0][nested_name] = None
                result.append(Mutation(f"null_nested_{field}_{nested_name}", "S1", json.dumps(nested_null, sort_keys=True)))
    if "selected_action_id" in value:
        invented = copy.deepcopy(value)
        invented["selected_action_id"] = "inactive"
        result.append(Mutation("invented_literal", "S2", json.dumps(invented, sort_keys=True)))
        malformed = copy.deepcopy(value)
        malformed["selected_action_id"] = "A1 because it is useful"
        result.append(Mutation("id_with_explanation", "S2", json.dumps(malformed, sort_keys=True)))
        if isinstance(value["selected_action_id"], str):
            spaced = copy.deepcopy(value)
            spaced["selected_action_id"] = f" {value['selected_action_id']} "
            result.append(Mutation("whitespace_wrapped_id", "S2", json.dumps(spaced, sort_keys=True)))
            lower = copy.deepcopy(value)
            lower["selected_action_id"] = value["selected_action_id"].lower()
            result.append(Mutation("case_variant_id", "S2", json.dumps(lower, sort_keys=True)))
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
        conclusion = {"step_type": "conclusion", "selected_action_id": None, "target_uncertainty": None, "expected_information_value": None, "why_this_action_now": None, "conclusion_hypothesis_id": "H1", "conclusion_reason": "The current evidence supports this conclusion.", "remaining_uncertainty_ids": []}
        result.append(Mutation("valid_conclusion_branch", "valid", json.dumps(conclusion, sort_keys=True)))
        stop = {"step_type": "stop_unresolved", "selected_action_id": None, "target_uncertainty": None, "expected_information_value": None, "why_this_action_now": None, "conclusion_hypothesis_id": None, "conclusion_reason": "The remaining uncertainty cannot be resolved with available enquiries.", "remaining_uncertainty_ids": ["H1:U1"]}
        result.append(Mutation("valid_stop_unresolved_branch", "valid", json.dumps(stop, sort_keys=True)))
        missing_stop_reason = {"step_type": "stop_unresolved", "remaining_uncertainty_ids": []}
        result.append(Mutation("stop_without_reason_or_ids", "S4", json.dumps(missing_stop_reason, sort_keys=True)))
        conclusion_with_action = {"step_type": "conclusion", "conclusion_hypothesis_id": "H1", "conclusion_reason": "A reason.", "selected_action_id": "A1"}
        result.append(Mutation("conclusion_with_action_field", "S4", json.dumps(conclusion_with_action, sort_keys=True)))
    if contract == "InitialExpansionResponse" and "competing_hypotheses" in value:
        mismatch = copy.deepcopy(value)
        mismatch["competing_hypotheses"][0]["parent_id"] = "H1"
        result.append(Mutation("competing_root_with_parent", "S4", json.dumps(mismatch, sort_keys=True)))
    if contract == "RevisionResponse":
        valid_update = copy.deepcopy(value)
        valid_update["hypothesis_updates"] = [{"hypothesis_id": "H1", "transition": "weaken", "reason": "New evidence reduces support for this hypothesis."}]
        result.append(Mutation("valid_state_update", "valid", json.dumps(valid_update, sort_keys=True)))
        valid_uncertainty = copy.deepcopy(value)
        valid_uncertainty["new_uncertainties"] = [{"id": "H1:U2", "hypothesis_id": "H1", "description": "A newly identified uncertainty.", "basis_evidence_ids": ["E1"]}]
        result.append(Mutation("valid_new_uncertainty", "valid", json.dumps(valid_uncertainty, sort_keys=True)))
        valid_refinement = copy.deepcopy(value)
        valid_refinement["uncertainty_updates"] = [{"uncertainty_id": "H1:U1", "transition": "refine", "reason": "The new evidence narrows the open question.", "new_description": "Which aspect of the claim remains unresolved?", "basis_evidence_ids": ["E1"]}]
        result.append(Mutation("valid_uncertainty_refinement", "valid", json.dumps(valid_refinement, sort_keys=True)))
        valid_resolution = copy.deepcopy(value)
        valid_resolution["uncertainty_updates"] = [{"uncertainty_id": "H1:U1", "transition": "resolve", "reason": "The new evidence resolves this uncertainty.", "basis_evidence_ids": ["E1"]}]
        result.append(Mutation("valid_uncertainty_resolution", "valid", json.dumps(valid_resolution, sort_keys=True)))
        valid_new_hypothesis = copy.deepcopy(value)
        valid_new_hypothesis["new_hypotheses"] = [{"id": "H2", "parent_id": None, "statement": "A newly considered explanation.", "status": "active", "supported_by": ["E1"], "conflicted_by": [], "unresolved": ["Which observation would distinguish this explanation?"], "specificity_basis_evidence_ids": ["E1"]}]
        result.append(Mutation("valid_new_hypothesis", "valid", json.dumps(valid_new_hypothesis, sort_keys=True)))
        mismatch = copy.deepcopy(value)
        mismatch["new_uncertainties"] = [{"id": "H1:U1", "hypothesis_id": "H2", "description": "A new uncertainty."}]
        result.append(Mutation("uncertainty_owner_mismatch", "S4", json.dumps(mismatch, sort_keys=True)))
        unknown_hypothesis = copy.deepcopy(value)
        unknown_hypothesis["hypothesis_updates"] = [{"hypothesis_id": "H999", "transition": "keep", "reason": "A reason."}]
        result.append(Mutation("unknown_hypothesis_reference", "S3", json.dumps(unknown_hypothesis, sort_keys=True)))
        unknown_uncertainty = copy.deepcopy(value)
        unknown_uncertainty["uncertainty_updates"] = [{"uncertainty_id": "H999:U1", "transition": "keep", "reason": "A reason."}]
        result.append(Mutation("unknown_uncertainty_reference", "S3", json.dumps(unknown_uncertainty, sort_keys=True)))
        unreleased = copy.deepcopy(value)
        unreleased["hypothesis_updates"] = [{"hypothesis_id": "H1", "transition": "keep", "reason": "A reason.", "add_supporting_evidence_ids": ["A2_RELEASE"]}]
        result.append(Mutation("unreleased_evidence_reference", "S3", json.dumps(unreleased, sort_keys=True)))
        wrong_hypothesis_namespace = copy.deepcopy(value)
        wrong_hypothesis_namespace["hypothesis_updates"] = [{"hypothesis_id": "E1", "transition": "keep", "reason": "A reason."}]
        result.append(Mutation("wrong_namespace_hypothesis_update", "S2", json.dumps(wrong_hypothesis_namespace, sort_keys=True)))
        wrong_uncertainty_namespace = copy.deepcopy(value)
        wrong_uncertainty_namespace["uncertainty_updates"] = [{"uncertainty_id": "E1", "transition": "keep", "reason": "A reason."}]
        result.append(Mutation("wrong_namespace_uncertainty_update", "S2", json.dumps(wrong_uncertainty_namespace, sort_keys=True)))
        duplicate_hypotheses = copy.deepcopy(value)
        proposal = {"id": "H2", "parent_id": None, "statement": "A new explanation.", "status": "active", "supported_by": ["E1"], "conflicted_by": [], "unresolved": ["A remaining question."], "specificity_basis_evidence_ids": []}
        duplicate_hypotheses["new_hypotheses"] = [proposal, copy.deepcopy(proposal)]
        result.append(Mutation("duplicate_new_hypothesis_id", "S4", json.dumps(duplicate_hypotheses, sort_keys=True)))
        duplicate_uncertainty = copy.deepcopy(value)
        duplicate_uncertainty["new_uncertainties"] = [{"id": "H1:U1", "hypothesis_id": "H1", "description": "A duplicate uncertainty.", "basis_evidence_ids": ["E1"]}]
        result.append(Mutation("duplicate_new_uncertainty_id", "S4", json.dumps(duplicate_uncertainty, sort_keys=True)))
    if contract in {"InitialResponse", "InitialExpansionResponse"} and "hypotheses" in value:
        empty = copy.deepcopy(value)
        empty["hypotheses"] = []
        result.append(Mutation("empty_hypotheses", "S1", json.dumps(empty, sort_keys=True)))
    if contract == "InitialExpansionResponse":
        unsupported = copy.deepcopy(value)
        unsupported["competing_hypotheses"][0]["relationship"] = "related"
        result.append(Mutation("unsupported_relationship", "S2", json.dumps(unsupported, sort_keys=True)))
        inactive = copy.deepcopy(value)
        inactive["competing_hypotheses"][0]["status"] = "weakened"
        result.append(Mutation("non_active_status", "S2", json.dumps(inactive, sort_keys=True)))
        scalar_namespace = copy.deepcopy(value)
        scalar_namespace["competing_hypotheses"][0]["contrasted_hypothesis_id"] = "E1"
        result.append(Mutation("wrong_namespace_contrasted_hypothesis", "S2", json.dumps(scalar_namespace, sort_keys=True)))
        specialization = copy.deepcopy(value)
        specialization["competing_hypotheses"][0].update({"parent_id": "H1", "relationship": "specialization", "contrasted_hypothesis_id": None, "material_difference": None, "specificity_basis_evidence_ids": ["E1"]})
        result.append(Mutation("valid_specialization_relationship", "valid", json.dumps(specialization, sort_keys=True)))
        wrong_parent = copy.deepcopy(specialization)
        wrong_parent["competing_hypotheses"][0]["parent_id"] = "E1"
        result.append(Mutation("wrong_namespace_parent_hypothesis", "S2", json.dumps(wrong_parent, sort_keys=True)))
        unknown_parent = copy.deepcopy(specialization)
        unknown_parent["competing_hypotheses"][0]["parent_id"] = "H999"
        result.append(Mutation("unknown_parent_hypothesis", "S3", json.dumps(unknown_parent, sort_keys=True)))
        unknown_contrast = copy.deepcopy(value)
        unknown_contrast["competing_hypotheses"][0]["contrasted_hypothesis_id"] = "H999"
        result.append(Mutation("unknown_contrast_hypothesis", "S3", json.dumps(unknown_contrast, sort_keys=True)))
        empty = copy.deepcopy(value)
        empty["competing_hypotheses"] = []
        result.append(Mutation("empty_competing_hypotheses", "S1", json.dumps(empty, sort_keys=True)))
        duplicate = copy.deepcopy(value)
        duplicate["competing_hypotheses"].append(copy.deepcopy(duplicate["competing_hypotheses"][0]))
        result.append(Mutation("duplicate_competing_hypothesis_id", "S4", json.dumps(duplicate, sort_keys=True)))
    if contract == "ModelScreenHypothesisResponse" and "hypotheses" in value:
        four = copy.deepcopy(value)
        while len(four["hypotheses"]) < 4:
            four["hypotheses"].append({"statement": f"Explanation {len(four['hypotheses']) + 1}.", "justification": "A supplied observation supports considering this possibility.", "uncertainty": "Its relative support remains uncertain."})
        result.append(Mutation("valid_four_hypotheses", "valid", json.dumps(four, sort_keys=True)))
        five = copy.deepcopy(four)
        five["hypotheses"].append({"statement": "Explanation 5.", "justification": "A supplied observation supports considering this possibility.", "uncertainty": "Its relative support remains uncertain."})
        result.append(Mutation("five_hypotheses", "S1", json.dumps(five, sort_keys=True)))
    if contract in {"InitialResponse", "InitialExpansionResponse"}:
        unknown = copy.deepcopy(value)
        target = unknown["hypotheses"][0] if "hypotheses" in unknown else unknown["competing_hypotheses"][0]
        target["supported_by"] = ["E999"]
        result.append(Mutation("unknown_evidence_reference", "S3", json.dumps(unknown, sort_keys=True)))
    if contract == "InitialResponse" and value.get("hypotheses"):
        inactive = copy.deepcopy(value)
        inactive["hypotheses"][0]["status"] = "weakened"
        result.append(Mutation("non_active_status", "S2", json.dumps(inactive, sort_keys=True)))
        duplicate = copy.deepcopy(value)
        duplicate["hypotheses"].append(copy.deepcopy(duplicate["hypotheses"][0]))
        result.append(Mutation("duplicate_hypothesis_id", "S4", json.dumps(duplicate, sort_keys=True)))
        self_parent = copy.deepcopy(value)
        self_parent["hypotheses"][0]["parent_id"] = self_parent["hypotheses"][0]["id"]
        result.append(Mutation("self_parent_hypothesis", "S4", json.dumps(self_parent, sort_keys=True)))
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
