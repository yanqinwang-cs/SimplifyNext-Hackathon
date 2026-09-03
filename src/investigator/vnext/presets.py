"""Trusted, small rule-preset selection for the vNext product path."""

from investigator.state.case_state import CaseState
from investigator.vnext.models import AssessmentRulePreset, ViolationDefinition


def academic_integrity_core_preset() -> AssessmentRulePreset:
    return AssessmentRulePreset(
        preset_id="academic-integrity-core",
        violations=[
            ViolationDefinition(
                violation_id="unauthorized_device",
                label="Unauthorized electronic device",
                rule_text="Unauthorized electronic devices are prohibited during the assessment.",
                prohibited_conduct="Possessing an unauthorized electronic device during the assessment.",
            ),
            ViolationDefinition(
                violation_id="unauthorized_external_communication",
                label="Unauthorized external communication",
                rule_text="External communication is prohibited during the assessment.",
                prohibited_conduct="Communicating externally during the assessment.",
            ),
            ViolationDefinition(
                violation_id="unauthorized_assistance",
                label="Unauthorized assistance",
                rule_text="External assistance is prohibited during the assessment.",
                prohibited_conduct="Receiving unauthorized assistance during the assessment.",
            ),
            ViolationDefinition(
                violation_id="prohibited_collaboration",
                label="Prohibited collaboration",
                rule_text="Collaboration is prohibited for this individual assessment.",
                prohibited_conduct="Collaborating with another person during the assessment.",
            ),
        ],
    )


PRESET_REGISTRY = {"academic-integrity-core": academic_integrity_core_preset}


def preset_for_case(case_state: CaseState) -> AssessmentRulePreset:
    try:
        return PRESET_REGISTRY[case_state.assessment_rule_preset_id]()
    except KeyError as exc:
        raise ValueError(
            f"Unknown vNext assessment rule preset: {case_state.assessment_rule_preset_id!r}"
        ) from exc
