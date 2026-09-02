"""Small mock adapter for the investigator workspace prototype."""

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class WorkspaceMessage:
    role: str
    content: str
    timestamp: str


@dataclass(frozen=True)
class EvidenceRequest:
    request_id: str
    information_sought: str
    reason: str
    status: str = "pending"


@dataclass(frozen=True)
class CaseWorkspaceState:
    case_id: str
    title: str
    status: str
    current_focus: str
    current_request: EvidenceRequest
    messages: tuple[WorkspaceMessage, ...]
    visible_sources: tuple[str, ...]
    current_line_of_enquiry: str
    active_explanations: tuple[str, ...] = field(default_factory=tuple)
    unresolved_questions: tuple[str, ...] = field(default_factory=tuple)
    case_history: tuple[str, ...] = field(default_factory=tuple)


def default_workspace(case_id: str = "case-01") -> CaseWorkspaceState:
    return CaseWorkspaceState(
        case_id=case_id,
        title="Business Law Tutorial 5",
        status="Investigating",
        current_focus="Whether Candidate A had access to an external information source during the assessment.",
        current_request=EvidenceRequest(
            request_id="request-01",
            information_sought="Obtain available evidence of Candidate A's device or communication activity during the assessment period.",
            reason="This could help determine whether the observed behaviour was associated with external information access.",
        ),
        messages=(
            WorkspaceMessage("investigator", "Reviewed the assessment script and invigilator report.", "10:21 AM"),
            WorkspaceMessage("simplifynext", "The current evidence leaves one important question open.", "10:22 AM"),
        ),
        visible_sources=("Assessment paper", "Student script", "Assessment rules", "Marker report", "Invigilator report", "Assessment logistics"),
        current_line_of_enquiry="Was external information accessed during the assessment?",
        active_explanations=("Permitted preparation may explain performance.", "Prohibited assistance may have contributed."),
        unresolved_questions=("Whether the observed behaviour has an alternative explanation.", "Whether further records are available for the assessment period."),
        case_history=("Case opened for investigator review.", "Initial assessment materials reviewed."),
    )


def get_case_workspace(case_id: str) -> CaseWorkspaceState:
    return default_workspace(case_id)


def _append_message(state: CaseWorkspaceState, content: str) -> CaseWorkspaceState:
    return replace(state, messages=(*state.messages, WorkspaceMessage("investigator", content, "Now")))


def provide_evidence(state: CaseWorkspaceState, note: str = "") -> CaseWorkspaceState:
    content = "Provided evidence for the current request."
    if note.strip():
        content += f" Note: {note.strip()}"
    return _append_message(replace(state, current_request=replace(state.current_request, status="fulfilled")), content)


def partially_fulfil(state: CaseWorkspaceState, unavailable: str = "") -> CaseWorkspaceState:
    content = "Partially fulfilled the current request."
    if unavailable.strip():
        content += f" Unavailable: {unavailable.strip()}"
    return _append_message(replace(state, current_request=replace(state.current_request, status="partially_fulfilled")), content)


def mark_unavailable(state: CaseWorkspaceState, reason: str = "") -> CaseWorkspaceState:
    content = "Marked the requested information unavailable."
    if reason.strip():
        content += f" Reason: {reason.strip()}"
    return _append_message(replace(state, current_request=replace(state.current_request, status="unavailable")), content)


def clarify_request(state: CaseWorkspaceState, question: str) -> CaseWorkspaceState:
    return _append_message(replace(state, current_request=replace(state.current_request, status="clarification_needed")), question.strip())


def redirect_investigation(state: CaseWorkspaceState, direction: str) -> CaseWorkspaceState:
    return _append_message(state, f"Redirected the investigation: {direction.strip()}")
