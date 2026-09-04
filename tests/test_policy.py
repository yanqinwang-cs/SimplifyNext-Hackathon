import pytest
from pydantic import ValidationError

from investigator.models import CaseParticipant
from investigator.policy import CASE_01_POLICY_PROFILE, PolicyProfile, PolicyRule, render_policy_profile


def rule(identifier="R1", disposition="PROHIBITED", parent=None, relation=None, context="closed_book_exam"):
    return PolicyRule(id=identifier, title="Rule", subject_roles=["candidate"], conduct_category="resource_use", resource_scope=["reference"], disposition=disposition, temporal_scope=["during_exam"], location_scope=["exam_venue"], context_scope=[context], parent_rule_id=parent, relation_to_parent=relation, text="Rule text")


def profile(rules):
    return PolicyProfile(profile_id="test", title="Test", participants=[CaseParticipant(id="PERSON1", contextual_roles=["candidate"], display_label="Candidate")], temporal_values={"during_exam"}, location_values={"exam_venue"}, context_values={"closed_book_exam", "exception"}, conduct_categories={"resource_use"}, rules=rules)


def test_closed_book_profile_and_prompt_rendering() -> None:
    rendered = render_policy_profile()
    assert CASE_01_POLICY_PROFILE.profile_id == "closed_book_computer_exam"
    assert "{{R1}}" in rendered and "{{R1.1}}" in rendered
    assert "local_llm" in rendered and "non_smart_watch" in rendered and "smartwatch" in rendered


def test_policy_duplicate_parent_cycle_and_scope_errors_are_rejected() -> None:
    with pytest.raises(ValidationError):
        profile([rule(), rule()])
    with pytest.raises(ValidationError):
        profile([rule("R1", parent="R2", relation="SPECIALIZES"), rule("R2", parent="R1", relation="SPECIALIZES")])
    with pytest.raises(ValidationError):
        profile([rule("R1", context="unknown")])


def test_policy_conflict_requires_explicit_exception() -> None:
    with pytest.raises(ValidationError):
        profile([rule("R1", "PROHIBITED"), rule("R2", "PERMITTED")])
    assert profile([rule("R1", "PROHIBITED"), rule("R1.1", "PERMITTED", parent="R1", relation="EXCEPTION_TO", context="exception")])
    with pytest.raises(ValidationError):
        profile([rule("R1", "PROHIBITED"), rule("R1.1", "PERMITTED", parent="R1", relation="SPECIALIZES")])


def test_participants_are_plural_contextual_entities_not_graph_nodes() -> None:
    profile_value = CASE_01_POLICY_PROFILE
    assert len(profile_value.participants) == 2
    assert {role for participant in profile_value.participants for role in participant.contextual_roles} >= {"candidate", "tutor", "staff_member"}
    with pytest.raises(ValidationError):
        CaseParticipant(id="PERSON1", contextual_roles=["cheater"], display_label="No")
