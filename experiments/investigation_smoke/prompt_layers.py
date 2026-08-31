GLOBAL_INVESTIGATION_RULES = """GLOBAL INVESTIGATION RULES
Reason about actions or conduct attributable to the student. Knowledge, intention, opportunity, anomaly, association, possession, and unusual performance do not by themselves establish misconduct. Knowledge and how it was acquired are separate; preserve source/provenance as uncertainty when unresolved.
Own proficiency includes the student's own thought, memory, reasoning, calculation, movement, speech, typing, and object manipulation. Assistance is an external information or capability contribution, broadly classified as PERSON or TOOL. Do not invent a mechanism unless context or released evidence supports it.
Permission is assessment-dependent: reason about action + resource/capability + policy + time, rather than external help automatically meaning misconduct. Use before/during/after assessment and venue/outside/unknown location when relevant. Keep broad explanations and distinguish observations from possible explanations."""


def render_current_case(case_input: str, prior_hypotheses: list[dict] | None = None, prior_uncertainties: list[dict] | None = None) -> str:
    parts = ["CURRENT CASE EVIDENCE / PRIOR STATE", case_input]
    if prior_hypotheses is not None:
        import json
        parts.append("Prior hypotheses/tree (NOT evidence):\n" + json.dumps(prior_hypotheses, indent=2))
        parts.append("Prior unresolved uncertainties:\n" + json.dumps(prior_uncertainties or [], indent=2) + "\n(NOT evidence)")
    return "\n\n".join(parts)


def render_assessment_layer(context_text: str) -> str:
    return "ASSESSMENT ONTOLOGY / POLICY\n" + context_text
