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

Generate an initial hypothesis tree grounded in the case information below. For each hypothesis provide id, optional parent_id, statement, status, supported_by evidence IDs, conflicted_by evidence IDs, unresolved uncertainty, and specificity_basis evidence IDs. Do not use hypothesis IDs in evidence-reference fields.

Then choose exactly one available enquiry. Choose the enquiry that would most usefully change the current explanation space by discriminating between active explanations or reducing consequential uncertainty. Do not choose an action because it proves a preferred hypothesis.

Return only JSON with keys hypotheses, selected_action_id, target_uncertainty, expected_information_value, and why_this_action_now.

Available enquiries:
{catalogue_text()}

Case information:
{visible_case_input()}"""


def revision_prompt(case_input: str, prior_state: dict, selected_action: dict, release_content: str) -> str:
    return f"""{RULES}

Revise the existing hypothesis tree after one controlled evidence release. Only the original case evidence plus the newly released artefact may justify revision. Do not rewrite history. Preserve broad parent hypotheses unless the new evidence genuinely conflicts with them. Narrow only when the new evidence supports increased specificity. If a narrow child is contradicted while its parent remains viable, weaken or remove the child and preserve or reactivate the parent. Do not invent a replacement mechanism merely because one branch failed. Unresolved uncertainty is a valid endpoint.

Return only JSON with keys hypothesis_updates, new_hypotheses, remaining_uncertainties, and revision_rationale. Use hypothesis_updates with hypothesis_id, transition (keep/weaken/conflict/remove/activate), and an audit reason. Do not select another enquiry.

Original visible case:
{case_input}

Prior structured hypothesis state (not evidence):
{json.dumps(prior_state, indent=2)}

Selected enquiry:
{json.dumps(selected_action, indent=2)}

Newly released artefact content:
{release_content}"""

