import json

from experiments.model_screen.cases import get_case, render_case
from experiments.investigation_smoke.case_01.catalog import ENQUIRY_CATALOG
from experiments.investigation_smoke.context import render_assessment_context
from experiments.investigation_smoke.prompt_layers import GLOBAL_INVESTIGATION_RULES, render_assessment_layer, render_current_case


RULES = GLOBAL_INVESTIGATION_RULES


def visible_case_input() -> str:
    return render_case(get_case("case_01"))


def catalogue_text() -> str:
    return "\n".join(f"{a.action_id} — {a.title}: {a.definition}" for a in ENQUIRY_CATALOG)


def initial_output_template() -> dict:
    return {
        "hypotheses": [{
            "id": "H1", "parent_id": None, "statement": "A broad evidence-grounded explanation.", "status": "active",
            "supported_by": ["E1"], "conflicted_by": [],
            "unresolved": ["An important uncertainty that remains unresolved."],
            "specificity_basis_evidence_ids": [],
        }],
        "selected_action_id": "A1",
        "target_uncertainty": "The uncertainty this enquiry addresses.",
        "expected_information_value": "How the result could change the explanation space.",
        "why_this_action_now": "Why this enquiry is useful now.",
    }


def revision_output_template(release_evidence_id: str) -> dict:
    return {
        "hypothesis_updates": [{
            "hypothesis_id": "H1", "transition": "keep",
            "reason": "Why this update is justified by the newly released evidence.",
            "add_supporting_evidence_ids": [release_evidence_id],
            "add_conflicting_evidence_ids": [], "add_specificity_basis_evidence_ids": [],
            "requested_operation_name": None, "requested_effect": None,
            "why_existing_operations_do_not_fit": None,
        }],
        "new_hypotheses": [],
        "uncertainty_updates": [{
            "uncertainty_id": "H1:U1", "transition": "refine",
            "reason": "Why this uncertainty changed.", "new_description": "A more precise unresolved question.",
            "basis_evidence_ids": [release_evidence_id],
            "requested_operation_name": None, "requested_effect": None,
            "why_existing_operations_do_not_fit": None,
        }],
        "new_uncertainties": [],
        "revision_rationale": "Short explanation of how the new evidence changed the state.",
    }


def initial_prompt() -> str:
    task = """TASK-SPECIFIC INSTRUCTION
Generate an initial hypothesis tree grounded in the case information below. Use exactly the schema field names; do not rename unresolved. Then choose exactly one available enquiry that most usefully changes the explanation space. Do not choose an action because it proves a preferred hypothesis. Use only released evidence; context resources are not evidence."""
    return f"""{RULES}

{render_assessment_layer(render_assessment_context())}

{render_current_case(visible_case_input())}

{task}

CANONICAL JSON OUTPUT TEMPLATE
Fill this one canonical JSON template:
{json.dumps(initial_output_template(), indent=2)}
Use bare canonical IDs only in ID fields. Use natural-language explanations only in text fields. Do not place explanations in fields ending with `_id`, `_ids`, or `_evidence_ids`.

Available enquiries:
{catalogue_text()}

"""


def revision_prompt(case_input: str, prior_hypotheses: list[dict], prior_uncertainties: list[dict], selected_action: dict, release_evidence_id: str, release_content: str) -> str:
    task = """TASK-SPECIFIC INSTRUCTION
Revise the existing hypothesis tree after one controlled evidence release. Only original case evidence plus the newly released artefact may justify revision. Preserve viable broad parents and narrow only when justified. Do not select another enquiry. Use uncertainty updates for unresolved questions; do not invent mechanisms."""
    return f"""{RULES}

{render_assessment_layer(render_assessment_context())}

{render_current_case(case_input, prior_hypotheses, prior_uncertainties)}

{task}

Return only JSON with exactly these keys and no extras. Fill the one canonical template below: do not add, remove, or rename fields. `reason` is REQUIRED for every hypothesis update and every uncertainty update. Use defined transitions only; use `other` as a last resort when none can faithfully express the update. All evidence-reference fields are arrays of bare IDs. Use `uncertainty_updates` to keep, refine, resolve, or remove an existing uncertainty ID, and `new_uncertainties` for newly identified uncertainties.

Canonical JSON template:
{json.dumps(revision_output_template(release_evidence_id), indent=2)}
ID FIELDS: bare canonical IDs only. TEXT FIELDS: natural-language explanations only. Do not place explanations in any field ending with `_id`, `_ids`, or `_evidence_ids`. Keep every array as an array even when empty. New uncertainty IDs must use the canonical H1:U1 form and belong to their declared hypothesis. `other` fields must be null unless the transition is `other`; `other` never executes arbitrary semantics.

Selected enquiry:
{json.dumps(selected_action, indent=2)}

Selected enquiry/action ID: {selected_action["action_id"]}
Newly released evidence ID: {release_evidence_id}
`{selected_action["action_id"]}` is an action ID; `{release_evidence_id}` is an evidence ID. Only original evidence IDs E1–E7 and the release evidence ID may appear in evidence-reference fields. The action ID must not appear in any evidence-reference field.

Newly released artefact content:
{release_content}"""
