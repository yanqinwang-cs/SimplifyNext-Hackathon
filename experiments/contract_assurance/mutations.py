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


def _wrong_namespace(field: str, value: str) -> str:
    """Return a syntactically plausible ID from a different declared namespace."""
    if "action" in field:
        return "E1"
    if "evidence" in field:
        return "A1"
    if "uncertainty" in field:
        return "H1"
    return "E1" if value.startswith("H") else "H1"


def mutations(value: dict[str, Any], *, required_fields: tuple[str, ...] = (), contract: str | None = None) -> list[Mutation]:
    result = [Mutation("empty", "S0", ""), Mutation("whitespace", "S0", "   "), Mutation("prose_only", "S0", "Here is the requested object.")]
    canonical = json.dumps(value, sort_keys=True)
    cut_positions = {
        "open": 1,
        "quarter": max(1, len(canonical) // 4),
        "midpoint": max(1, len(canonical) // 2),
        "before_close": max(1, len(canonical) - 2),
    }
    result.extend([
        Mutation("malformed_json", "S0", canonical[:-1]),
        Mutation("trailing_comma", "S0", canonical[:-1] + ",}"),
        Mutation("duplicate_braces", "S0", "{" + canonical + "}"),
        Mutation("prose_before_json", "S0", "Here is the object:\n" + canonical),
        Mutation("trailing_material", "S0", canonical + "\nDone."),
        Mutation("multiple_json_objects", "S0", canonical + "\n" + canonical),
        Mutation("json_array_top_level", "S0", "[" + canonical + "]"),
        Mutation("json_null_top_level", "S0", "null"),
        Mutation("json_string_top_level", "S0", '"response"'),
        Mutation("json_number_top_level", "S0", "7"),
        Mutation("empty_fence", "S0", "```json\n```"),
        Mutation("broken_opening_fence", "S0", "```json\n" + canonical + "\n"),
        Mutation("broken_closing_fence", "S0", canonical + "\n```"),
        Mutation("fenced_json", "valid", "```json\n" + canonical + "\n```"),
    ])
    result.extend(
        Mutation(f"truncated_json_{name}", "S0", canonical[:position])
        for name, position in cut_positions.items()
    )
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
    # Exercise shape boundaries for optional fields too.  These mutations are
    # deliberately generic: the production schema, not this harness, decides
    # whether a nullable/optional value is allowed.
    for field, original in value.items():
        if field in required_fields:
            continue
        if original is None:
            continue
        if isinstance(original, list):
            wrong_shapes = [("scalar", "not an array"), ("object", {}), ("null", None)]
        elif isinstance(original, dict):
            wrong_shapes = [("array", []), ("scalar", "not an object"), ("null", None)]
        elif isinstance(original, str):
            wrong_shapes = [("primitive", 7)]
        elif isinstance(original, bool):
            wrong_shapes = [("primitive", "not a boolean")]
        else:
            wrong_shapes = [("primitive", "not the declared primitive")]
        for shape_name, replacement in wrong_shapes:
            wrong_shape = copy.deepcopy(value)
            wrong_shape[field] = replacement
            result.append(Mutation(f"wrong_shape_{field}_{shape_name}", "S1", json.dumps(wrong_shape, sort_keys=True)))
        renamed = copy.deepcopy(value)
        del renamed[field]
        renamed[f"{field}_value"] = original
        result.append(Mutation(f"renamed_field_{field}", "S1", json.dumps(renamed, sort_keys=True)))
        if isinstance(original, str) and (field == "id" or field.endswith("_id")):
            wrong_namespace = copy.deepcopy(value)
            wrong_namespace[field] = _wrong_namespace(field, original)
            result.append(Mutation(f"wrong_namespace_{field}", "S2", json.dumps(wrong_namespace, sort_keys=True)))
            spaced_id = copy.deepcopy(value)
            spaced_id[field] = f" {original} "
            result.append(Mutation(f"whitespace_wrapped_{field}", "S2", json.dumps(spaced_id, sort_keys=True)))
            case_variant = copy.deepcopy(value)
            case_variant[field] = original.lower()
            result.append(Mutation(f"case_variant_{field}", "S2", json.dumps(case_variant, sort_keys=True)))
    extra = copy.deepcopy(value)
    extra["unexpected_field"] = "x"
    result.append(Mutation("unexpected_field", "S1", json.dumps(extra, sort_keys=True)))
    result.append(Mutation("extra_wrapper", "S1", json.dumps({"response": value}, sort_keys=True)))
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
            for nested_name, nested_value in nested_type[field][0].items():
                if isinstance(nested_value, str) and (nested_name == "id" or nested_name.endswith("_id")):
                    wrong_namespace = copy.deepcopy(value)
                    wrong_namespace[field][0][nested_name] = _wrong_namespace(nested_name, nested_value)
                    result.append(Mutation(f"wrong_namespace_{field}_{nested_name}", "S2", json.dumps(wrong_namespace, sort_keys=True)))
                    spaced_id = copy.deepcopy(value)
                    spaced_id[field][0][nested_name] = f" {nested_value} "
                    result.append(Mutation(f"whitespace_wrapped_{field}_{nested_name}", "S2", json.dumps(spaced_id, sort_keys=True)))
                    case_variant = copy.deepcopy(value)
                    case_variant[field][0][nested_name] = nested_value.lower()
                    result.append(Mutation(f"case_variant_{field}_{nested_name}", "S2", json.dumps(case_variant, sort_keys=True)))
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
        if contract in {"NextActionResponse", "InitialResponse", "InitialExpansionResponse"} and value["selected_action_id"] == "A1":
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
    sentinel_by_field = {
        "target_uncertainty": "The uncertainty this enquiry addresses.",
        "expected_information_value": "How the result could change the explanation space.",
        "why_this_action_now": "Why this enquiry is useful now.",
        "revision_rationale": "How the evidence changed the state.",
    }
    for field, sentinel in sentinel_by_field.items():
        if field in value:
            sentinel_value = copy.deepcopy(value)
            sentinel_value[field] = sentinel
            result.append(Mutation(f"canonical_sentinel_{field}", "S4", json.dumps(sentinel_value, sort_keys=True)))
    if "step_type" in value:
        polluted = copy.deepcopy(value)
        polluted["step_type"] = "action"
        polluted["conclusion_hypothesis_id"] = "H1"
        result.append(Mutation("mixed_union_branch", "S4", json.dumps(polluted, sort_keys=True)))
        unsupported_step = copy.deepcopy(value)
        unsupported_step["step_type"] = "pause"
        result.append(Mutation("unsupported_step_type", "S2", json.dumps(unsupported_step, sort_keys=True)))
    if contract == "NextStepResponse":
        action_with_conclusion_reason = {
            "step_type": "action",
            "selected_action_id": "A1",
            "target_uncertainty": "H1:U1",
            "expected_information_value": "high",
            "why_this_action_now": "This enquiry best distinguishes the live hypotheses.",
            "conclusion_hypothesis_id": None,
            "conclusion_reason": "The current evidence supports this conclusion.",
            "remaining_uncertainty_ids": [],
        }
        result.append(Mutation("action_with_conclusion_reason", "S4", json.dumps(action_with_conclusion_reason, sort_keys=True)))
        conclusion_with_remaining_ids = {
            "step_type": "conclusion",
            "selected_action_id": None,
            "target_uncertainty": None,
            "expected_information_value": None,
            "why_this_action_now": None,
            "conclusion_hypothesis_id": "H1",
            "conclusion_reason": "The current evidence supports this conclusion.",
            "remaining_uncertainty_ids": ["H1:U1"],
        }
        result.append(Mutation("conclusion_with_remaining_uncertainties", "S4", json.dumps(conclusion_with_remaining_ids, sort_keys=True)))
        stop_with_conclusion_id = {
            "step_type": "stop_unresolved",
            "selected_action_id": None,
            "target_uncertainty": None,
            "expected_information_value": None,
            "why_this_action_now": None,
            "conclusion_hypothesis_id": "H1",
            "conclusion_reason": "The remaining uncertainty cannot be resolved with available enquiries.",
            "remaining_uncertainty_ids": ["H1:U1"],
        }
        result.append(Mutation("stop_unresolved_with_conclusion_id", "S4", json.dumps(stop_with_conclusion_id, sort_keys=True)))
        stop_with_action_fields = {
            "step_type": "stop_unresolved",
            "selected_action_id": "A1",
            "target_uncertainty": "An open question.",
            "expected_information_value": "It may distinguish explanations.",
            "why_this_action_now": "It is available now.",
            "conclusion_hypothesis_id": None,
            "conclusion_reason": "The remaining uncertainty cannot be resolved with available enquiries.",
            "remaining_uncertainty_ids": ["H1:U1"],
        }
        result.append(Mutation("stop_unresolved_with_action_fields", "S4", json.dumps(stop_with_action_fields, sort_keys=True)))
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
        unsupported_hypothesis_transition = copy.deepcopy(value)
        unsupported_hypothesis_transition["hypothesis_updates"] = [{"hypothesis_id": "H1", "transition": "deprioritize", "reason": "The current evidence lowers priority."}]
        result.append(Mutation("unsupported_hypothesis_transition", "S2", json.dumps(unsupported_hypothesis_transition, sort_keys=True)))
        unsupported_uncertainty_transition = copy.deepcopy(value)
        unsupported_uncertainty_transition["uncertainty_updates"] = [{"uncertainty_id": "H1:U1", "transition": "defer", "reason": "The issue can be revisited later."}]
        result.append(Mutation("unsupported_uncertainty_transition", "S2", json.dumps(unsupported_uncertainty_transition, sort_keys=True)))
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
        valid_other = copy.deepcopy(value)
        valid_other["hypothesis_updates"] = [{"hypothesis_id": "H1", "transition": "other", "reason": "The available operations do not represent this requested change.", "requested_operation_name": "reframe", "requested_effect": "Preserve the current hypothesis while changing its framing.", "why_existing_operations_do_not_fit": {"keep": "It does not capture the requested framing change."}}]
        result.append(Mutation("valid_other_hypothesis_operation", "valid", json.dumps(valid_other, sort_keys=True)))
        other_with_evidence = copy.deepcopy(valid_other)
        other_with_evidence["hypothesis_updates"][0]["add_supporting_evidence_ids"] = ["E1"]
        result.append(Mutation("other_hypothesis_with_evidence_fields", "S4", json.dumps(other_with_evidence, sort_keys=True)))
        valid_remove = copy.deepcopy(value)
        valid_remove["hypothesis_updates"] = [{"hypothesis_id": "H1", "transition": "remove", "reason": "The current evidence no longer warrants retaining this hypothesis."}]
        result.append(Mutation("valid_hypothesis_removal", "valid", json.dumps(valid_remove, sort_keys=True)))
        valid_conflict = copy.deepcopy(value)
        valid_conflict["hypothesis_updates"] = [{"hypothesis_id": "H1", "transition": "conflict", "reason": "New evidence conflicts with this hypothesis.", "add_conflicting_evidence_ids": ["E1"]}]
        result.append(Mutation("valid_hypothesis_conflict", "valid", json.dumps(valid_conflict, sort_keys=True)))
        valid_activate = copy.deepcopy(value)
        valid_activate["hypothesis_updates"] = [{"hypothesis_id": "H1", "transition": "activate", "reason": "The current evidence supports reactivating this hypothesis.", "add_supporting_evidence_ids": ["E1"]}]
        result.append(Mutation("valid_hypothesis_activation", "valid", json.dumps(valid_activate, sort_keys=True)))
        invalid_other = copy.deepcopy(value)
        invalid_other["hypothesis_updates"] = [{"hypothesis_id": "H1", "transition": "other", "reason": "A reason."}]
        result.append(Mutation("other_without_operation_details", "S4", json.dumps(invalid_other, sort_keys=True)))
        leaked_hypothesis_other_fields = copy.deepcopy(value)
        leaked_hypothesis_other_fields["hypothesis_updates"] = [{"hypothesis_id": "H1", "transition": "keep", "reason": "A reason.", "requested_operation_name": "reframe", "requested_effect": "Change framing.", "why_existing_operations_do_not_fit": {"keep": "It does not change framing."}}]
        result.append(Mutation("hypothesis_other_fields_on_keep", "S4", json.dumps(leaked_hypothesis_other_fields, sort_keys=True)))
        valid_uncertainty_other = copy.deepcopy(value)
        valid_uncertainty_other["uncertainty_updates"] = [{"uncertainty_id": "H1:U1", "transition": "other", "reason": "The requested uncertainty operation is not represented by existing transitions.", "requested_operation_name": "merge", "requested_effect": "Combine this uncertainty with a related unresolved issue.", "why_existing_operations_do_not_fit": {"refine": "Refinement would preserve separate uncertainty records."}}]
        result.append(Mutation("valid_other_uncertainty_operation", "valid", json.dumps(valid_uncertainty_other, sort_keys=True)))
        other_uncertainty_with_evidence = copy.deepcopy(valid_uncertainty_other)
        other_uncertainty_with_evidence["uncertainty_updates"][0]["basis_evidence_ids"] = ["E1"]
        result.append(Mutation("other_uncertainty_with_evidence_fields", "S4", json.dumps(other_uncertainty_with_evidence, sort_keys=True)))
        invalid_uncertainty_other = copy.deepcopy(value)
        invalid_uncertainty_other["uncertainty_updates"] = [{"uncertainty_id": "H1:U1", "transition": "other", "reason": "A reason."}]
        result.append(Mutation("other_uncertainty_without_operation_details", "S4", json.dumps(invalid_uncertainty_other, sort_keys=True)))
        leaked_uncertainty_other_fields = copy.deepcopy(value)
        leaked_uncertainty_other_fields["uncertainty_updates"] = [{"uncertainty_id": "H1:U1", "transition": "keep", "reason": "A reason.", "requested_operation_name": "merge", "requested_effect": "Combine issues.", "why_existing_operations_do_not_fit": {"keep": "It preserves separate issues."}}]
        result.append(Mutation("uncertainty_other_fields_on_keep", "S4", json.dumps(leaked_uncertainty_other_fields, sort_keys=True)))
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
        cross_environment_release = copy.deepcopy(value)
        cross_environment_release["hypothesis_updates"] = [{"hypothesis_id": "H1", "transition": "keep", "reason": "A reason.", "add_supporting_evidence_ids": ["A3_RELEASE"]}]
        result.append(Mutation("cross_environment_release_reference", "S3", json.dumps(cross_environment_release, sort_keys=True)))
        hidden_release = copy.deepcopy(value)
        hidden_release["hypothesis_updates"] = [{"hypothesis_id": "H1", "transition": "keep", "reason": "A reason.", "add_supporting_evidence_ids": ["A1_RELEASE"]}]
        result.append(Mutation("hidden_release_reference", "S3", json.dumps(hidden_release, sort_keys=True)))
        unknown_update_evidence = copy.deepcopy(value)
        unknown_update_evidence["hypothesis_updates"] = [{"hypothesis_id": "H1", "transition": "keep", "reason": "A reason.", "add_supporting_evidence_ids": ["E999"]}]
        result.append(Mutation("unknown_update_evidence_reference", "S3", json.dumps(unknown_update_evidence, sort_keys=True)))
        source_id_as_evidence = copy.deepcopy(value)
        source_id_as_evidence["hypothesis_updates"] = [{"hypothesis_id": "H1", "transition": "keep", "reason": "A reason.", "add_supporting_evidence_ids": ["case_01_visible"]}]
        result.append(Mutation("source_id_in_evidence_reference", "S2", json.dumps(source_id_as_evidence, sort_keys=True)))
        unknown_uncertainty_evidence = copy.deepcopy(value)
        unknown_uncertainty_evidence["uncertainty_updates"] = [{"uncertainty_id": "H1:U1", "transition": "keep", "reason": "A reason.", "basis_evidence_ids": ["E999"]}]
        result.append(Mutation("unknown_uncertainty_evidence_reference", "S3", json.dumps(unknown_uncertainty_evidence, sort_keys=True)))
        unknown_new_hypothesis_evidence = copy.deepcopy(value)
        unknown_new_hypothesis_evidence["new_hypotheses"] = [{"id": "H2", "parent_id": None, "statement": "A new explanation.", "status": "active", "supported_by": ["E999"], "conflicted_by": [], "unresolved": ["A remaining question."], "specificity_basis_evidence_ids": []}]
        result.append(Mutation("unknown_new_hypothesis_evidence_reference", "S3", json.dumps(unknown_new_hypothesis_evidence, sort_keys=True)))
        unknown_new_uncertainty_evidence = copy.deepcopy(value)
        unknown_new_uncertainty_evidence["new_uncertainties"] = [{"id": "H1:U2", "hypothesis_id": "H1", "description": "A new uncertainty.", "basis_evidence_ids": ["E999"]}]
        result.append(Mutation("unknown_new_uncertainty_evidence_reference", "S3", json.dumps(unknown_new_uncertainty_evidence, sort_keys=True)))
        wrong_hypothesis_namespace = copy.deepcopy(value)
        wrong_hypothesis_namespace["hypothesis_updates"] = [{"hypothesis_id": "E1", "transition": "keep", "reason": "A reason."}]
        result.append(Mutation("wrong_namespace_hypothesis_update", "S2", json.dumps(wrong_hypothesis_namespace, sort_keys=True)))
        wrong_uncertainty_namespace = copy.deepcopy(value)
        wrong_uncertainty_namespace["uncertainty_updates"] = [{"uncertainty_id": "E1", "transition": "keep", "reason": "A reason."}]
        result.append(Mutation("wrong_namespace_uncertainty_update", "S2", json.dumps(wrong_uncertainty_namespace, sort_keys=True)))
        refine_without_description = copy.deepcopy(value)
        refine_without_description["uncertainty_updates"] = [{"uncertainty_id": "H1:U1", "transition": "refine", "reason": "A reason."}]
        result.append(Mutation("refine_without_description", "S4", json.dumps(refine_without_description, sort_keys=True)))
        refine_with_other_fields = copy.deepcopy(value)
        refine_with_other_fields["uncertainty_updates"] = [{"uncertainty_id": "H1:U1", "transition": "refine", "reason": "A reason.", "new_description": "A narrower question.", "requested_operation_name": "merge", "requested_effect": "Combine issues.", "why_existing_operations_do_not_fit": {"refine": "It preserves separate issues."}}]
        result.append(Mutation("refine_with_other_fields", "S4", json.dumps(refine_with_other_fields, sort_keys=True)))
        resolve_with_description = copy.deepcopy(value)
        resolve_with_description["uncertainty_updates"] = [{"uncertainty_id": "H1:U1", "transition": "resolve", "reason": "A reason.", "new_description": "Contradictory detail."}]
        result.append(Mutation("resolve_with_description", "S4", json.dumps(resolve_with_description, sort_keys=True)))
        keep_with_description = copy.deepcopy(value)
        keep_with_description["uncertainty_updates"] = [{"uncertainty_id": "H1:U1", "transition": "keep", "reason": "A reason.", "new_description": "A narrower question."}]
        result.append(Mutation("keep_with_description", "S4", json.dumps(keep_with_description, sort_keys=True)))
        remove_with_description = copy.deepcopy(value)
        remove_with_description["uncertainty_updates"] = [{"uncertainty_id": "H1:U1", "transition": "remove", "reason": "A reason.", "new_description": "A narrower question."}]
        result.append(Mutation("remove_with_description", "S4", json.dumps(remove_with_description, sort_keys=True)))
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
        self_contrast = copy.deepcopy(value)
        self_contrast["competing_hypotheses"][0]["contrasted_hypothesis_id"] = self_contrast["competing_hypotheses"][0]["id"]
        result.append(Mutation("competing_root_self_contrast", "S4", json.dumps(self_contrast, sort_keys=True)))
        missing_difference = copy.deepcopy(value)
        missing_difference["competing_hypotheses"][0]["material_difference"] = None
        result.append(Mutation("competing_root_without_material_difference", "S4", json.dumps(missing_difference, sort_keys=True)))
        specialization = copy.deepcopy(value)
        specialization["competing_hypotheses"][0].update({"parent_id": "H1", "relationship": "specialization", "contrasted_hypothesis_id": None, "material_difference": None, "specificity_basis_evidence_ids": ["E1"]})
        result.append(Mutation("valid_specialization_relationship", "valid", json.dumps(specialization, sort_keys=True)))
        specialization_with_competitor_fields = copy.deepcopy(specialization)
        specialization_with_competitor_fields["competing_hypotheses"][0].update({"contrasted_hypothesis_id": "H1", "material_difference": "It differs from the parent."})
        result.append(Mutation("specialization_with_competing_root_fields", "S4", json.dumps(specialization_with_competitor_fields, sort_keys=True)))
        self_parent = copy.deepcopy(specialization)
        self_parent["competing_hypotheses"][0]["parent_id"] = self_parent["competing_hypotheses"][0]["id"]
        result.append(Mutation("specializing_self_parent", "S4", json.dumps(self_parent, sort_keys=True)))
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
        reserved_id = copy.deepcopy(value)
        reserved_id["competing_hypotheses"][0]["id"] = "H1"
        reserved_id["competing_hypotheses"][0]["contrasted_hypothesis_id"] = "H1"
        result.append(Mutation("duplicate_reserved_seed_hypothesis_id", "S4", json.dumps(reserved_id, sort_keys=True)))
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
        cycle = copy.deepcopy(value)
        first = cycle["hypotheses"][0]
        second = copy.deepcopy(first)
        second["id"] = "H2"
        second["parent_id"] = "H1"
        first["parent_id"] = "H2"
        cycle["hypotheses"] = [first, second]
        result.append(Mutation("cycle_introducing_hypotheses", "S4", json.dumps(cycle, sort_keys=True)))
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
