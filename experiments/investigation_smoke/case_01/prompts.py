import json

from experiments.model_screen.cases import get_case, render_case
from experiments.investigation_smoke.case_01.catalog import ENQUIRY_CATALOG


RULES = """Use only released evidence. Previous hypotheses are not evidence and must not be cited as support.
Keep hypotheses at the broadest level that is still decision-useful. Do not specify a mechanism, actor, direction, device, tool, location, source, communication channel, or sequence unless released evidence supports that specificity.
Use conditional language for unestablished mechanisms. Do not decide whether misconduct occurred, assign numerical confidence, or propose a final verdict.
Distinguish observations from possible explanations. Prefer a small number of broad competing explanations."""


def visible_case_input() -> str:
    return render_case(get_case("case_01"))


def catalogue_text() -> str:
    return "\n".join(f"{a.action_id} — {a.title}: {a.definition}" for a in ENQUIRY_CATALOG)


def initial_prompt() -> str:
    return f"""{RULES}

Generate an initial hypothesis tree grounded in the case information below. Use exactly these field names; do not rename unresolved. Arrays must remain arrays even when empty. Do not add extra fields. Do not use hypothesis IDs in evidence-reference fields.

Then choose exactly one available enquiry. Choose the enquiry that would most usefully change the current explanation space by discriminating between active explanations or reducing consequential uncertainty. Do not choose an action because it proves a preferred hypothesis.

Return only JSON matching this shape:
{{
  "hypotheses": [{{
    "id": "H1", "parent_id": null, "statement": "A broad evidence-grounded explanation.", "status": "active",
    "supported_by": ["E1"], "conflicted_by": [],
    "unresolved": ["An important uncertainty that remains unresolved."], "specificity_basis": []
  }}],
  "selected_action_id": "A1",
  "target_uncertainty": "The uncertainty this enquiry addresses.",
  "expected_information_value": "How the result could change the explanation space.",
  "why_this_action_now": "Why this enquiry is useful now."
}}

Available enquiries:
{catalogue_text()}

Case information:
{visible_case_input()}"""


def revision_prompt(case_input: str, prior_hypotheses: list[dict], prior_uncertainties: list[dict], selected_action: dict, release_evidence_id: str, release_content: str) -> str:
    return f"""{RULES}

Revise the existing hypothesis tree after one controlled evidence release. Only the original case evidence plus the newly released artefact may justify revision. Do not rewrite history. Preserve broad parent hypotheses unless the new evidence genuinely conflicts with them. Narrow only when the new evidence supports increased specificity. If a narrow child is contradicted while its parent remains viable, weaken or remove the child and preserve or reactivate the parent. Do not invent a replacement mechanism merely because one branch failed. Unresolved uncertainty is a valid endpoint.

Return only JSON with exactly these keys and no extras. `reason` is REQUIRED for every hypothesis update. All additive evidence fields are arrays. Use exactly these field names. Attach the newly released artefact evidence ID only where it genuinely supports, conflicts with, or justifies narrowing a hypothesis; not every update needs it. Do not select another enquiry.

Example JSON shape:
{{
  "hypothesis_updates": [{{
    "hypothesis_id": "H1", "transition": "keep",
    "reason": "Why this update is justified by the newly released evidence.",
    "add_supporting_evidence_ids": ["{release_evidence_id}"],
    "add_conflicting_evidence_ids": [], "add_specificity_basis": []
  }}],
  "new_hypotheses": [],
  "remaining_uncertainties": ["An important uncertainty still unresolved after this release."],
  "revision_rationale": "Short explanation of how the new evidence changed the state."
}}

Original visible case:
{case_input}

Prior hypotheses/tree (NOT evidence):
{json.dumps(prior_hypotheses, indent=2)}

Prior unresolved uncertainties:
{json.dumps(prior_uncertainties, indent=2)}

Selected enquiry:
{json.dumps(selected_action, indent=2)}

Selected enquiry/action ID: {selected_action["action_id"]}
Newly released evidence ID: {release_evidence_id}
`{selected_action["action_id"]}` is an action ID; `{release_evidence_id}` is an evidence ID. Only original evidence IDs E1–E7 and the release evidence ID may appear in evidence-reference fields. The action ID must not appear in any evidence-reference field.

Newly released artefact content:
{release_content}"""
