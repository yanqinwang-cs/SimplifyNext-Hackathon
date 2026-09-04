from typing import Literal

from pydantic import BaseModel, ConfigDict

from investigator.models import CaseParticipant


class AssistanceResource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: Literal["person", "tool"]
    label: str
    capabilities: list[str]
    permitted_uses: list[str]
    prohibited_uses: list[str]
    temporal_bounds: list[str]
    locations: list[str]


class AssessmentContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessment_type: str
    format: str
    duration: str
    question_count: int
    location: str
    own_proficiency: list[str]
    general_rules: list[str]
    resources: list[AssistanceResource]
    participants: list[CaseParticipant]


CASE_01_ASSESSMENT_CONTEXT = AssessmentContext(
    assessment_type="quantitative methods examination",
    format="90-minute closed-book, 40-question multiple-choice examination",
    duration="90 minutes",
    question_count=40,
    location="examination venue",
    own_proficiency=["memory", "reasoning", "mental calculation", "physical movement", "manipulation of permitted examination objects"],
    general_rules=[
        "External assistance or communication during the examination is prohibited.",
        "Ordinary stationery and an opaque pencil case are permitted during the examination.",
        "A resource listed here is context, not evidence that the student accessed or used it.",
        "Policy status depends on the action, capability, time, and relevant assessment rule.",
        "Location constrains possibilities but does not establish permission or misconduct.",
    ],
    resources=[
        AssistanceResource(
            id="RESOURCE_TUTOR", type="person", label="Private tutor",
            capabilities=["teach concepts", "explain material", "provide practice", "answer study questions"],
            permitted_uses=["ordinary tutoring and preparation before the examination"],
            prohibited_uses=["providing assistance during the closed-book examination"],
            temporal_bounds=["before assessment", "during assessment"], locations=["outside examination venue", "unknown"],
        ),
        AssistanceResource(
            id="RESOURCE_STATIONERY", type="tool", label="Ordinary stationery / opaque pencil case",
            capabilities=["ordinary physical use and storage of permitted stationery"],
            permitted_uses=["use during the examination as permitted"], prohibited_uses=[],
            temporal_bounds=["during assessment"], locations=["examination venue"],
        ),
        AssistanceResource(
            id="RESOURCE_DEVICE", type="tool", label="Phone / smartwatch",
            capabilities=["stored information", "communication", "computation depending on device"],
            permitted_uses=[], prohibited_uses=["possession or use contrary to the surrender rule during the examination"],
            temporal_bounds=["before assessment", "during assessment"], locations=["examination venue", "unknown"],
        ),
    ],
    participants=[
        CaseParticipant(id="PERSON1", contextual_roles=["candidate", "student", "author"], display_label="Candidate 1"),
        CaseParticipant(id="PERSON2", contextual_roles=["candidate", "student", "coauthor"], display_label="Candidate 2"),
        CaseParticipant(id="PERSON3", contextual_roles=["tutor", "staff_member"], display_label="Tutor or staff member"),
    ],
)


def render_assessment_context(context: AssessmentContext = CASE_01_ASSESSMENT_CONTEXT) -> str:
    lines = [
        f"Assessment: {context.assessment_type}; {context.format}; location: {context.location}.",
        "Own proficiency includes: " + ", ".join(context.own_proficiency) + ".",
        "Rules: " + " ".join(context.general_rules),
        "Known assistance/resource catalogue (context only; not evidence):",
    ]
    lines.append("Case participants (contextual roles, not conclusions):")
    for participant in context.participants:
        lines.append(f"- {{{{{participant.id}}}}}: {participant.display_label}; roles={', '.join(participant.contextual_roles)}")
    for resource in context.resources:
        lines.append(f"- {resource.id} [{resource.type}] {resource.label}")
        lines.append(f"  capabilities: {', '.join(resource.capabilities)}")
        lines.append(f"  permitted: {', '.join(resource.permitted_uses) or 'none stated'}")
        lines.append(f"  prohibited: {', '.join(resource.prohibited_uses) or 'none stated'}")
        lines.append(f"  time/location: {', '.join(resource.temporal_bounds)} / {', '.join(resource.locations)}")
    lines.append("The catalogue defines capabilities and permission boundaries; released evidence establishes whether access or use actually occurred.")
    return "\n".join(lines)
