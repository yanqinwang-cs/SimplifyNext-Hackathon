"""Small mock adapter for the investigator workspace."""
from datetime import datetime


def default_workspace() -> dict:
    return {
        "case_id": "CASE-01", "title": "Business Law Tutorial 5", "status": "Active",
        "current_focus": "Whether external assistance could explain the unusual assessment pattern.",
        "current_line": "Whether Candidate A had access to an external information source during the assessment.",
        "latest_update": "The submitted answers show uneven performance and the invigilator observed repeated glasses adjustments, but the mechanism remains unresolved.",
        "request": {"request_id": "REQ-01", "information_sought": "Obtain available evidence of Candidate A's device or communication activity during the assessment period.", "reason": "This could help determine whether the observed behaviour was associated with external information access.", "status": "pending"},
        "sources": ["Assessment paper", "Student script", "Assessment rules", "Marker report", "Invigilator report", "Assessment logistics"],
        "unresolved": ["Was any external information source available during the assessment?", "Can the observed behaviour be explained independently of misconduct?"],
        "explanations": ["External assistance may have contributed to some assessment answers.", "The observed behaviour may have an ordinary non-assistance explanation."],
        "activity": [
            {"time": "11:20", "actor": "Investigator", "title": "Reviewed case material", "summary": "Reviewed the assessment script and invigilator report."},
            {"time": "11:22", "actor": "Case updated", "title": "Unresolved question identified", "summary": "The mechanism behind the unusual pattern remains unresolved."},
            {"time": "11:23", "actor": "Evidence requested", "title": "Information request opened", "summary": "Available records about assessment-period information access."},
        ],
    }


def get_case_workspace(case_id: str) -> dict:
    workspace = default_workspace()
    workspace["case_id"] = case_id
    return workspace


def _record(workspace: dict, actor: str, title: str, summary: str) -> None:
    workspace["activity"].append({"time": datetime.now().strftime("%H:%M"), "actor": actor, "title": title, "summary": summary})


def provide_evidence(workspace: dict, uploaded_name: str, note: str) -> None:
    workspace["request"]["status"] = "fulfilled"
    workspace["sources"].append(uploaded_name or "New case source")
    _record(workspace, "Human investigator", "Evidence received", note or "A source was added for investigation.")
    workspace["latest_update"] = "New case information was received; the investigation can resume with the source available for review."


def partially_fulfil(workspace: dict, uploaded_name: str, unavailable: str, note: str) -> None:
    workspace["request"]["status"] = "partially_fulfilled"
    if uploaded_name:
        workspace["sources"].append(uploaded_name)
    _record(workspace, "Human investigator", "Request partially fulfilled", unavailable or note or "Some requested information was unavailable.")


def mark_unavailable(workspace: dict, reason: str) -> None:
    workspace["request"]["status"] = "unavailable"
    _record(workspace, "Human investigator", "Information unavailable", reason or "No relevant institutional record was identified.")


def clarify_request(workspace: dict, question: str) -> None:
    workspace["request"]["status"] = "clarification_needed"
    _record(workspace, "Human investigator", "Request clarification", question or "Please clarify the period or information needed.")


def redirect_investigation(workspace: dict, focus: str) -> None:
    workspace["current_line"] = focus or workspace["current_line"]
    _record(workspace, "Human investigator", "Investigation redirected", focus or "The investigator selected a different line of enquiry.")


def set_status(workspace: dict, status: str) -> None:
    workspace["status"] = status
    _record(workspace, "Human investigator", f"Case {status.lower()}", f"The case is now {status.lower()}; the current state was preserved.")
