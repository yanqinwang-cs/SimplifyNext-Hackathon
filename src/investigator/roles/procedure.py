"""Small, shared role-procedure contract used by prompts and pre-call checks.

The graph operation registry remains the authority for mechanical edge legality;
this module records the semantic reason an operation exists and when it is
appropriate.  Keeping this metadata separate from provider code makes it
usable by both offline tests and live prompt builders.
"""

from dataclasses import dataclass

from investigator.graph import EdgeRelation, GraphNodeType, OperationSpecRegistry


@dataclass(frozen=True)
class ProcedureSpec:
    operation: str
    purpose: str
    prerequisites: str
    rationale: str
    invalid_substitute: str
    valid_alternative: str
    role: str


INVESTIGATOR_PROCEDURE = (
    ProcedureSpec("add_evidence", "Record a direct observation grounded in readable raw sources.", "At least one relevant readable source is available.", "Preserves raw-source provenance before semantic inference.", "Deriving a proposition directly from a SOURCE.", "add_evidence(source) -> add_proposition from E/P if inference is justified.", "investigator"),
    ProcedureSpec("add_proposition", "State a smaller truth-apt inference.", "A legal active EVIDENCE or PROPOSITION basis is already determined.", "Separates inference from the record that supports it.", "Using a raw SOURCE or UNCERTAINTY as a derivation basis.", "add_evidence first, then add_proposition.", "investigator"),
    ProcedureSpec("add_hypothesis", "Create a broader explanation when no suitable existing hypothesis can be reused.", "A materially useful explanation is already formulated.", "Keeps competing explanations explicit.", "Creating a hypothesis merely because a source exists.", "Reuse an equivalent HYPOTHESIS or add supporting E/P first.", "investigator"),
    ProcedureSpec("add_uncertainty", "Record a consequential unresolved question.", "The question is materially relevant and has a legal active target.", "Questions guide the next investigation without pretending to be claims.", "Using an uncertainty as evidence or a proposition.", "Represent an answer as EVIDENCE or PROPOSITION.", "investigator"),
    ProcedureSpec("add_support", "Record positive evidential bearing on a proposition or hypothesis.", "A legal E/P source and P/H target are already identified.", "Makes evidential bearing auditable.", "Using SUPPORTS as a substitute for inferential provenance.", "Use add_derivation for why a proposition exists.", "investigator"),
    ProcedureSpec("add_conflict", "Record materially incompatible evidence or claim.", "A legal E/P source and P/H target are materially incompatible.", "Distinguishes contradiction from mere reduced confidence or alternatives.", "Calling an alternative explanation or unresolved question a conflict.", "Represent alternatives as propositions/hypotheses.", "investigator"),
    ProcedureSpec("add_derivation", "Record why a proposition was inferred.", "The proposition and legal E/P basis already exist.", "Captures inferential provenance separately from evidential bearing.", "Using SUPPORTS merely to explain derivation.", "add_evidence -> add_proposition -> add_derivation.", "investigator"),
    ProcedureSpec("add_specialization", "Connect a specific explanation to a broader parent.", "Both active hypotheses and the child-parent relationship are already determined.", "Preserves explanatory hierarchy.", "Inventing a parent after selecting the operation.", "Add or reuse the broader hypothesis first.", "investigator"),
    ProcedureSpec("move_focus", "Move the current area of Investigator attention.", "The destination is a legal canonical graph node.", "Guides attention without changing graph visibility or global case management.", "Directly changing global focus.", "request_steward_review.", "investigator"),
    ProcedureSpec("request_open", "Ask for useful context when targeted specificity is not earned.", "Useful information is needed but no precise target/need/value is justified.", "Provides a safe lower-specificity path without invented precision.", "An incomplete targeted evidence request.", "request_evidence once its prerequisites are known.", "investigator"),
    ProcedureSpec("request_evidence", "Ask for a targeted human evidence response.", "A specific active uncertainty, precise need, reason, and expected value are already formulated.", "Makes the human request auditable and decision-relevant.", "Choosing the request first and inventing its fields afterward.", "request_open until targeted prerequisites are satisfied.", "investigator"),
    ProcedureSpec("request_steward_review", "Ask for global case-management review.", "Local reasoning is exhausted or global reassessment is materially useful.", "Separates global attention management from local extraction.", "Investigator mutating distant/global focus.", "Continue locally when useful work remains.", "investigator"),
    ProcedureSpec("request_enquiry", "Select one explicitly available legacy predefined enquiry.", "A listed action and addressable active local uncertainty are already identified.", "Preserves the existing Stage 1 path.", "Inventing an action or targeting an unavailable uncertainty.", "request_evidence or request_open when a human request is appropriate.", "investigator"),
    ProcedureSpec("continue_local", "Continue bounded local reasoning.", "At least one graph update is ready.", "Prevents no-op turns.", "Continuing without graph work.", "local_exhausted or request_steward_review.", "investigator"),
    ProcedureSpec("local_exhausted", "Declare that the local frontier has no useful next step.", "Local useful work is genuinely exhausted.", "Creates a controlled Steward boundary.", "Using it while useful local work remains.", "continue_local or request_steward_review.", "investigator"),
)

STEWARD_PROCEDURE = (
    ProcedureSpec("keep_focus", "Retain a productive current focus.", "No materially better global branch requires attention.", "Avoids unnecessary focus churn.", "Shifting without a better branch.", "shift_focus when another branch is materially better.", "steward"),
    ProcedureSpec("shift_focus", "Move global attention to a materially better graph branch.", "A valid active destination is identified and globally more useful.", "Manages global attention and monitors neglected branches.", "Investigator directly changing global focus.", "request_steward_review.", "steward"),
    ProcedureSpec("generalize", "Retreat from an unsupported specific child to a viable parent.", "An active child-parent SPECIALIZES edge exists; child is no longer justified; parent remains viable.", "Preserves the broader explanation while dropping unsupported specificity.", "Selecting generalize before finding a parent.", "keep_focus or archive the stale child.", "steward"),
    ProcedureSpec("archive", "Remove a stale or superseded branch from active attention while retaining history.", "The target is stale/superseded; current-focus archive has an active destination.", "Keeps historical state auditable without active distraction.", "Deleting or silently mutating a branch.", "generalize or keep_focus when the branch remains useful.", "steward"),
    ProcedureSpec("reactivate", "Return archived material when it becomes materially relevant.", "Archived target is identified and newly relevant.", "Allows historical evidence/branches to re-enter active reasoning.", "Creating a duplicate replacement branch.", "shift_focus or keep_focus if it is already active.", "steward"),
    ProcedureSpec("stop_unresolved", "Hand an unresolved frontier to the human decision-maker after review.", "Trusted global frontier assessed; no useful region/action remains; local work is exhausted.", "Makes unresolved termination explicit without having Steward decide guilt.", "Stopping because evidence is merely unavailable or confidence is reduced.", "keep_focus, shift_focus, or request further local work.", "steward"),
    ProcedureSpec("request_open", "Ask the human for specific useful case context when a targeted evidence request is not earned.", "A concrete information need and expected investigative value are stated.", "Preserves bounded human-in-the-loop recovery without graph mutation.", "A vague request for more information.", "request_evidence when one active uncertainty and a precise source need are justified.", "steward"),
    ProcedureSpec("request_evidence", "Ask the human for a targeted source that addresses one active uncertainty.", "One active uncertainty, precise information need, reason, and expected value are stated.", "Makes Steward recovery auditable without changing the graph.", "Inventing a target or using an action ID as evidence.", "request_open when targeted prerequisites are not satisfied.", "steward"),
)

INVESTIGATOR_INTENT_COVERAGE = {
    "record direct source observation": "add_evidence",
    "infer proposition": "add_proposition",
    "create/reuse explanation": "add_hypothesis",
    "create unresolved question": "add_uncertainty",
    "express support": "add_support",
    "express conflict": "add_conflict",
    "add inferential derivation": "add_derivation",
    "specialize explanation": "add_specialization",
    "move local focus": "move_focus",
    "ask broad/open question": "request_open",
    "ask targeted evidence question": "request_evidence",
    "select legacy predefined enquiry": "request_enquiry",
    "request global review": "request_steward_review",
    "continue local reasoning": "continue_local",
}

STEWARD_INTENT_COVERAGE = {
    "keep current focus": "keep_focus",
    "shift global focus": "shift_focus",
    "generalize": "generalize",
    "archive": "archive",
    "reactivate": "reactivate",
    "handoff": "stop_unresolved",
    "ask broad human question": "request_open",
    "ask targeted human question": "request_evidence",
}

GRAPH_OPERATION_RELATIONS = {
    "add_proposition": EdgeRelation.DERIVED_FROM,
    "add_support": EdgeRelation.SUPPORTS,
    "add_conflict": EdgeRelation.CONFLICTS,
    "add_uncertainty": EdgeRelation.TARGETS,
    "add_derivation": EdgeRelation.DERIVED_FROM,
    "add_specialization": EdgeRelation.SPECIALIZES,
}


def procedure_for(role: str) -> tuple[ProcedureSpec, ...]:
    if role == "investigator":
        return INVESTIGATOR_PROCEDURE
    if role == "steward":
        return STEWARD_PROCEDURE
    raise ValueError(f"Unknown role: {role!r}")


def render_procedure(role: str) -> str:
    lines = ["Reason first, operation second. Choose an operation only after its prerequisites are satisfied."]
    for spec in procedure_for(role):
        lines.append(f"- {spec.operation}: purpose={spec.purpose} prerequisites={spec.prerequisites} why={spec.rationale} invalid_substitute={spec.invalid_substitute} valid_alternative={spec.valid_alternative}")
    return "\n".join(lines)


def procedural_contract_errors() -> list[str]:
    errors: list[str] = []
    for role, coverage, specs in (("investigator", INVESTIGATOR_INTENT_COVERAGE, INVESTIGATOR_PROCEDURE), ("steward", STEWARD_INTENT_COVERAGE, STEWARD_PROCEDURE)):
        by_operation = {item.operation: item for item in specs}
        for intent, operation in coverage.items():
            item = by_operation.get(operation)
            if item is None:
                errors.append(f"{role} intent {intent!r} has no procedure for {operation!r}")
            elif not all((item.purpose, item.prerequisites, item.rationale, item.valid_alternative)):
                errors.append(f"{role} operation {operation!r} has incomplete procedure metadata")
    for operation, relation in GRAPH_OPERATION_RELATIONS.items():
        if not OperationSpecRegistry.get(relation).allowed_pairs:
            errors.append(f"{operation!r} has no mechanical OperationSpecRegistry legality")
    if not OperationSpecRegistry.allows(EdgeRelation.DERIVED_FROM, GraphNodeType.PROPOSITION, GraphNodeType.EVIDENCE):
        errors.append("add_proposition/add_derivation do not agree with DERIVED_FROM legality")
    return errors


def correction_guidance(error: Exception) -> str:
    """Return procedural feedback without suggesting a case conclusion."""
    message = str(error)
    if "SOURCE" in message or "source" in message and "proposition" in message:
        return "A raw SOURCE is not a semantic graph claim; use add_evidence first, then derive a proposition from legal E/P nodes."
    if "uncertainty" in message.lower() and "basis" in message.lower():
        return "An UNCERTAINTY is a question, not an evidential or derivational basis; obtain an EVIDENCE/PROPOSITION answer first."
    return "Re-evaluate the intended semantic effect and satisfy the operation prerequisites before selecting the operation."
