"""Deterministic model-facing grammar for vNext graph proposals.

The operation and reference matrix is generated from the same registry used by
the Graph Warden.  The additional scope and source-applicability rules below
are semantic constraints that are intentionally not encoded in that matrix.
"""

from __future__ import annotations

from typing import Any

from investigator.graph import OPERATION_CONTRACTS, OPERATION_SPECS, OperationSpecRegistry
from investigator.vnext.models import InvestigatorProposal


_RELATION_BY_OPERATION = {
    "add_proposition": "DERIVED_FROM",
    "add_uncertainty": "TARGETS",
    "add_support": "SUPPORTS",
    "add_conflict": "CONFLICTS",
    "add_derivation": "DERIVED_FROM",
    "add_specialization": "SPECIALIZES",
}

_OPERATION_RULES = {
    "add_evidence": (
        "Creates EVIDENCE.",
        "Required: non-empty statement; one or more admitted raw source_ids; source_ids must be actual admitted SourceRegistry records and unique.",
        "Scope: CASE only when source applicability allows case-shared use; SUBJECT only for a configured student to whom the source applies; RELATIONSHIP only when one source is valid for that relationship and jointly covers every participant.",
        "Forbidden: widening student-specific evidence to CASE; assigning A-only evidence to B; assigning one-student evidence to RELATIONSHIP; combining separate A-only and B-only sources into relationship evidence; unknown or invented source IDs; duplicate source IDs; invented source records.",
    ),
    "add_proposition": (
        "Creates PROPOSITION.",
        "derived_from_node_ids may reference only the types listed below and must contain at least one unique derivation source.",
        "Every DERIVED_FROM edge means the new proposition is derived from the referenced source and must obey directional scope compatibility.",
        "Forbidden: HYPOTHESIS or UNCERTAINTY as a derivation source; SUBJECT-A -> SUBJECT-B; SUBJECT -> RELATIONSHIP; SUBJECT/RELATIONSHIP -> CASE; unrelated RELATIONSHIP -> SUBJECT; stitching separate student-private evidence into a relationship proposition; duplicate derived_from refs.",
    ),
    "add_hypothesis": (
        "Creates HYPOTHESIS only. No implicit provenance or support is created by adding the node.",
        "Scope must be legal for the configured case and students.",
        "Forbidden: treating hypothesis creation as evidence; using relationship participation as proof; widening private material.",
    ),
    "add_uncertainty": (
        "Creates UNCERTAINTY, with the target_node_id represented semantically as UNCERTAINTY -> TARGETS -> target.",
        "target_node_id may reference only the types listed below.",
        "Forbidden: targeting UNCERTAINTY unless the canonical registry is changed to allow it; unknown refs; incompatible scope; private-scope widening.",
    ),
    "add_support": (
        "Creates SUPPORTS.",
        "source_node_id and target_node_id may use only the types listed below.",
        "Forbidden: HYPOTHESIS -> SUPPORTS -> HYPOTHESIS; HYPOTHESIS or UNCERTAINTY as source; EVIDENCE as target; incompatible scopes; private evidence supporting another student's node. If one hypothesis conceptually depends on another, do not fake it with add_support; omit the edge unless an exposed legal operation applies.",
    ),
    "add_conflict": (
        "Creates CONFLICTS.",
        "source_node_id and target_node_id may use only the types listed below.",
        "Forbidden: HYPOTHESIS or UNCERTAINTY as source; EVIDENCE or UNCERTAINTY as target; incompatible scopes; cross-student private leakage.",
    ),
    "add_derivation": (
        "Creates DERIVED_FROM. The direction is derived PROPOSITION -> DERIVED_FROM -> source material.",
        "derived_proposition_id must be a PROPOSITION; source_node_id must be EVIDENCE or PROPOSITION.",
        "Forbidden: hypothesis or uncertainty refs; inverted direction; incompatible scopes; student-private -> RELATIONSHIP widening; private -> CASE widening.",
    ),
    "add_specialization": (
        "Creates SPECIALIZES. The direction is child HYPOTHESIS -> SPECIALIZES -> parent HYPOTHESIS.",
        "child_hypothesis_id and parent_hypothesis_id must both be HYPOTHESIS.",
        "Forbidden: any non-hypothesis endpoint; unrelated or incompatible scopes; using specialization as evidentiary support.",
    ),
}


def _proposal_operation_names() -> set[str]:
    """Extract operation literals from the production proposal schema."""

    schema = InvestigatorProposal.model_json_schema()
    definitions = schema.get("$defs", {})
    names: set[str] = set()
    items = schema.get("properties", {}).get("graph_updates", {}).get("items", {})
    for variant in items.get("oneOf", []):
        ref = variant.get("$ref", "")
        definition = definitions.get(ref.rsplit("/", 1)[-1], {})
        operation = definition.get("properties", {}).get("operation", {})
        value = operation.get("const")
        if isinstance(value, str):
            names.add(value)
    return names


def _type_names(values: set[Any] | frozenset[Any]) -> str:
    return ", ".join(sorted(value.value.upper() for value in values))


def model_facing_graph_grammar() -> str:
    """Return the complete deterministic graph contract for model prompts."""

    operation_names = sorted(_proposal_operation_names())
    registry_names = set(OPERATION_CONTRACTS)
    if set(_OPERATION_RULES) != set(operation_names):
        raise RuntimeError("Model-facing graph grammar is out of sync with InvestigatorProposal operations")
    if not set(operation_names) <= registry_names:
        raise RuntimeError("Model-facing graph grammar references an unregistered operation")

    sections = [
        "COMPLETE LEGAL GRAPH OPERATION CONTRACT",
        "This is the authoritative graph grammar. Anything outside this contract is forbidden.",
        "Use only already-known node IDs or unique local_ref values created earlier in this proposal. Never reference an unknown or unconstructed node, substitute a semantically unrelated node merely to satisfy a type check, or invent a source/relationship.",
        "Local relationship refs such as R1 are allowed where the schema permits them; never use persistent internal relationship IDs.",
        "",
    ]
    for operation in operation_names:
        contract = OperationSpecRegistry.contract(operation)
        created = contract.created_type.value.upper() if contract.created_type else "RELATION"
        sections.append(f"{operation}: creates {created}")
        for line in _OPERATION_RULES[operation]:
            sections.append(f"- {line}")
        if contract.references:
            sections.append("- Allowed references (generated from OperationSpecRegistry):")
            for reference in contract.references:
                cardinality = " (array; keep it unique)" if reference.itemized else ""
                sections.append(f"  - {reference.field}: {_type_names(reference.allowed_types)}{cardinality}")
        relation = _RELATION_BY_OPERATION.get(operation)
        if relation:
            spec = next(item for item in OPERATION_SPECS.values() if item.relation.value == relation.lower())
            pairs = ", ".join(
                f"{source.value.upper()} -> {target.value.upper()}"
                for source, target in sorted(spec.allowed_pairs, key=lambda pair: (pair[0].value, pair[1].value))
            )
            sections.append(f"- Canonical relation type pairs (generated from OperationSpecRegistry): {pairs}")
        sections.append("")

    sections.extend(
        [
            "DIRECTIONAL SCOPE GRAMMAR",
            "Scope legality is directional, not symmetric:",
            "- CASE -> CASE: legal; CASE -> SUBJECT: legal; CASE -> RELATIONSHIP: legal.",
            "- SUBJECT A -> SUBJECT A: legal; SUBJECT A -> SUBJECT B: illegal.",
            "- SUBJECT -> CASE: illegal; SUBJECT -> RELATIONSHIP: illegal.",
            "- RELATIONSHIP R -> the same RELATIONSHIP R: legal; RELATIONSHIP R -> a participating SUBJECT: legal; RELATIONSHIP R -> an unrelated SUBJECT: illegal; RELATIONSHIP -> CASE: illegal; RELATIONSHIP R1 -> RELATIONSHIP R2: illegal unless they are the same canonical relationship.",
            "The legacy case_subject compatibility marker is internal only; do not teach it as a normal vNext scope.",
            "",
            "RELATIONSHIP AND SOURCE APPLICABILITY",
            "- Use supplied local relationship refs such as R1. Do not invent a relationship unless the schema permits a relationship proposal and one admitted source jointly identifies every participant.",
            "- Multiple separate student-private sources do not satisfy the joint-source requirement. Relationship participation defines scope/association only; it does not establish collaboration, communication, knowledge, receipt, intent, use, or misconduct.",
            "- CASE_SHARED sources may contribute case-wide and narrow into relevant student/relationship reasoning where scope compatibility permits.",
            "- STUDENT_SPECIFIC sources may contribute only to that student; they cannot be widened to CASE or converted into relationship evidence.",
            "- MULTI_STUDENT_CANDIDATE or trusted relationship-valid sources may be used only where the deterministic applicability and relationship rules permit.",
            "- A SINGLE_STUDENT_DEFAULT source is relevant only to the one configured student.",
            "",
            "FAIL-SAFE",
            "IF YOU ARE UNSURE WHETHER A GRAPH OPERATION IS LEGAL, OMIT IT. A smaller valid graph is preferred over a more complete invalid graph. You do not need to encode every reasoning step as a graph operation; use reasoning_summary for reasoning that does not require a graph edge. Never invent a legal-looking dependency solely to encode prose reasoning.",
        ]
    )
    return "\n".join(sections)


def model_facing_operation_names() -> set[str]:
    """Expose the production-derived operation set for focused consistency tests."""

    return _proposal_operation_names()
