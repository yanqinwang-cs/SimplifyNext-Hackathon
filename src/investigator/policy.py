import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from investigator.models import CaseParticipant


PolicyRelation = Literal["SPECIALIZES", "EXCEPTION_TO"]
Disposition = Literal["PERMITTED", "PROHIBITED"]


class PolicyRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    subject_roles: list[str] = Field(min_length=1)
    conduct_category: str
    resource_scope: list[str] = Field(min_length=1)
    disposition: Disposition
    temporal_scope: list[str] = Field(min_length=1)
    location_scope: list[str] = Field(min_length=1)
    context_scope: list[str] = Field(min_length=1)
    parent_rule_id: str | None = None
    relation_to_parent: PolicyRelation | None = None
    text: str

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not re.fullmatch(r"R\d+(?:\.\d+)*", value):
            raise ValueError("policy rule IDs must use the R1 or R1.1 namespace")
        return value

    @model_validator(mode="after")
    def validate_parent_relation(self) -> "PolicyRule":
        if (self.parent_rule_id is None) != (self.relation_to_parent is None):
            raise ValueError("parent_rule_id and relation_to_parent must be supplied together")
        return self


class PolicyProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: str
    title: str
    participants: list[CaseParticipant] = Field(min_length=1)
    temporal_values: set[str] = Field(min_length=1)
    location_values: set[str] = Field(min_length=1)
    context_values: set[str] = Field(min_length=1)
    conduct_categories: set[str] = Field(min_length=1)
    rules: list[PolicyRule] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_rules(self) -> "PolicyProfile":
        ids = [rule.id for rule in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("policy rule IDs must be unique")
        rule_by_id = {rule.id: rule for rule in self.rules}
        roles = {role for participant in self.participants for role in participant.contextual_roles}
        for rule in self.rules:
            if not set(rule.subject_roles) <= roles:
                raise ValueError(f"unknown participant role in policy rule {rule.id}")
            if not set(rule.temporal_scope) <= self.temporal_values or not set(rule.location_scope) <= self.location_values or not set(rule.context_scope) <= self.context_values or rule.conduct_category not in self.conduct_categories:
                raise ValueError(f"unknown policy scope value in rule {rule.id}")
            if rule.parent_rule_id and rule.parent_rule_id not in rule_by_id:
                raise ValueError(f"unknown parent rule: {rule.parent_rule_id}")
        for rule in self.rules:
            seen: set[str] = set()
            current = rule
            while current.parent_rule_id:
                if current.id in seen:
                    raise ValueError("policy rule parent cycle detected")
                seen.add(current.id)
                current = rule_by_id[current.parent_rule_id]
        for index, left in enumerate(self.rules):
            for right in self.rules[index + 1:]:
                if left.disposition == right.disposition or not _overlap(left, right):
                    continue
                explicit_exception = (left.relation_to_parent == "EXCEPTION_TO" and left.parent_rule_id == right.id) or (right.relation_to_parent == "EXCEPTION_TO" and right.parent_rule_id == left.id)
                if not explicit_exception:
                    raise ValueError(f"contradictory overlapping policy rules: {left.id} and {right.id}")
        return self


def _overlap(left: PolicyRule, right: PolicyRule) -> bool:
    return all(set(a) & set(b) for a, b in ((left.subject_roles, right.subject_roles), (left.resource_scope, right.resource_scope), (left.temporal_scope, right.temporal_scope), (left.location_scope, right.location_scope), (left.context_scope, right.context_scope))) and left.conduct_category == right.conduct_category


CASE_01_POLICY_PROFILE = PolicyProfile(
    profile_id="closed_book_computer_exam", title="Fictional closed-book computer examination",
    participants=[CaseParticipant(id="PERSON1", contextual_roles=["candidate", "student"], display_label="Candidate"), CaseParticipant(id="PERSON2", contextual_roles=["tutor", "staff_member"], display_label="Tutor or staff member")],
    temporal_values={"before_exam", "during_exam", "after_exam"}, location_values={"exam_venue", "outside_exam_venue", "unknown"}, context_values={"closed_book_exam", "approved_a4_sheet", "exam_environment"}, conduct_categories={"resource_use", "communication", "assistance", "viewing_work", "preparation"},
    rules=[
        PolicyRule(id="R1", title="External reference materials prohibited", subject_roles=["candidate"], conduct_category="resource_use", resource_scope=["external_reference"], disposition="PROHIBITED", temporal_scope=["during_exam"], location_scope=["exam_venue"], context_scope=["closed_book_exam"], text="External reference materials are prohibited during the closed-book examination."),
        PolicyRule(id="R1.1", title="One approved A4 reference sheet permitted", subject_roles=["candidate"], conduct_category="resource_use", resource_scope=["external_reference"], disposition="PERMITTED", temporal_scope=["during_exam"], location_scope=["exam_venue"], context_scope=["approved_a4_sheet"], parent_rule_id="R1", relation_to_parent="EXCEPTION_TO", text="Exactly one approved A4 sheet may be used on both sides when the profile allows it."),
        PolicyRule(id="R2", title="Approved examination computer permitted", subject_roles=["candidate"], conduct_category="resource_use", resource_scope=["approved_exam_computer"], disposition="PERMITTED", temporal_scope=["during_exam"], location_scope=["exam_venue"], context_scope=["exam_environment"], text="One approved examination computer is permitted for approved software and materials."),
        PolicyRule(id="R3", title="Network and electronic communication prohibited", subject_roles=["candidate"], conduct_category="communication", resource_scope=["internet", "wifi", "bluetooth", "cellular", "messaging"], disposition="PROHIBITED", temporal_scope=["during_exam"], location_scope=["exam_venue"], context_scope=["closed_book_exam"], text="Network access and electronic communication are prohibited."),
        PolicyRule(id="R4", title="Non-smart watch permitted", subject_roles=["candidate"], conduct_category="resource_use", resource_scope=["non_smart_watch"], disposition="PERMITTED", temporal_scope=["during_exam"], location_scope=["exam_venue"], context_scope=["exam_environment"], text="A non-smart watch without storage, network, or communication is permitted."),
        PolicyRule(id="R5", title="Smart watches and unauthorised devices prohibited", subject_roles=["candidate"], conduct_category="resource_use", resource_scope=["smartwatch", "phone", "tablet", "second_computer", "smart_glasses", "earbuds", "communication_device", "external_storage"], disposition="PROHIBITED", temporal_scope=["during_exam"], location_scope=["exam_venue"], context_scope=["exam_environment"], text="Smart watches and unauthorised electronic devices are prohibited from use and possession where specified by the examination rule."),
        PolicyRule(id="R6", title="Candidate communication prohibited", subject_roles=["candidate"], conduct_category="communication", resource_scope=["speaking", "whispering", "written_message", "deliberate_signal", "electronic_message"], disposition="PROHIBITED", temporal_scope=["during_exam"], location_scope=["exam_venue"], context_scope=["exam_environment"], text="Candidate-to-candidate communication and information transfer are prohibited."),
        PolicyRule(id="R7", title="Viewing another candidate work prohibited", subject_roles=["candidate"], conduct_category="viewing_work", resource_scope=["other_candidate_work"], disposition="PROHIBITED", temporal_scope=["during_exam"], location_scope=["exam_venue"], context_scope=["exam_environment"], text="Viewing or copying another candidate's examination work is prohibited."),
        PolicyRule(id="R8", title="Generative AI use prohibited", subject_roles=["candidate"], conduct_category="resource_use", resource_scope=["remote_llm", "local_llm", "generative_ai"], disposition="PROHIBITED", temporal_scope=["during_exam"], location_scope=["exam_venue"], context_scope=["closed_book_exam"], text="Remote or local generative AI use is prohibited, even without internet."),
        PolicyRule(id="R9", title="Obtaining or giving assistance prohibited", subject_roles=["candidate"], conduct_category="assistance", resource_scope=["person_assistance", "joint_solving", "copying"], disposition="PROHIBITED", temporal_scope=["during_exam"], location_scope=["exam_venue"], context_scope=["exam_environment"], text="Obtaining, giving, or jointly producing answers is prohibited."),
        PolicyRule(id="R10", title="Prior tutoring and study permitted", subject_roles=["candidate", "tutor"], conduct_category="preparation", resource_scope=["tutoring", "practice", "memorized_knowledge"], disposition="PERMITTED", temporal_scope=["before_exam"], location_scope=["outside_exam_venue", "unknown"], context_scope=["exam_environment"], text="Prior legitimate tutoring, study, and practice are not misconduct by themselves."),
    ],
)


def render_policy_profile(profile: PolicyProfile = CASE_01_POLICY_PROFILE) -> str:
    lines = [f"Profile: {profile.title} ({profile.profile_id})", "Policy rules are fictional context, not evidence. Cite existing IDs in the form {{R...}}; do not invent or rewrite rules."]
    for rule in profile.rules:
        parent = f"; {rule.relation_to_parent} {{{rule.parent_rule_id}}}" if rule.parent_rule_id else ""
        lines.append(f"- {{{{{rule.id}}}}} {rule.title}: {rule.disposition}; roles={','.join(rule.subject_roles)}; conduct={rule.conduct_category}; resources={','.join(rule.resource_scope)}; time={','.join(rule.temporal_scope)}; location={','.join(rule.location_scope)}; context={','.join(rule.context_scope)}. {rule.text}{parent}")
    return "\n".join(lines)
