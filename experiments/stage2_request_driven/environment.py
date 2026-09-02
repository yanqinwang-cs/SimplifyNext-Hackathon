"""Controlled human-response environment; hidden matching is audit-only."""
from dataclasses import dataclass
from typing import Any

from investigator.models.evidence_request import EvidenceRequestResponse
from investigator.models.source import Source
from investigator.services.evidence_requests import HumanEvidenceWorkflow

from .matcher import ControlledEvidenceMatcher, MatchResult, VisibleMatch


@dataclass(frozen=True)
class Fulfilment:
    request_id: str
    status: str
    source_id: str | None
    match: MatchResult
    note: str
    workflow_status: str
    visible_check: VisibleMatch


class ControlledEvidenceEnvironment:
    def __init__(self, workflow: HumanEvidenceWorkflow) -> None:
        self.workflow = workflow
        self.matcher = ControlledEvidenceMatcher()
        self.released: set[str] = set()

    def visible_sources(self, case_id: str) -> list[Source]:
        """Return the exact admitted raw records readable by the Investigator."""
        return list(self.workflow.repository.load(case_id).sources.values())

    def respond(self, case_id: str, request: Any) -> Fulfilment:
        visible_sources = self.visible_sources(case_id)
        visible_check = self.matcher.visible_match(request.information_sought, [(source.name, source.content) for source in visible_sources])
        if visible_check.answerable:
            note = "Responsive information is already present in current case sources. No additional source was released."
            self.workflow.respond(case_id, request.request_id, EvidenceRequestResponse(request_id=request.request_id, status="unavailable", note=note))
            return Fulfilment(request.request_id, "no_new_source", None, MatchResult(None, "none", "Hidden matcher was not invoked after visible-source match."), note, "unavailable", visible_check)
        match = self.matcher.match(request.information_sought, self.released)
        if match.fixture is None:
            note = "The requested material is unavailable; this is not evidence of absence."
            self.workflow.respond(case_id, request.request_id, EvidenceRequestResponse(request_id=request.request_id, status="unavailable", note=note))
            return Fulfilment(request.request_id, "unavailable", None, match, note, "unavailable", visible_check)
        fixture = match.fixture
        completed = self.workflow.respond(case_id, request.request_id, EvidenceRequestResponse(request_id=request.request_id, status="fulfilled", note="Controlled fixture supplied."), [{"display_name": fixture.filename, "content": fixture.content, "metadata": {"controlled_fixture": True, "fixture_key": fixture.key}}])
        self.released.add(fixture.key)
        return Fulfilment(request.request_id, "fulfilled", completed.released_source_ids[0], match, "Supplied as a raw source; no graph evidence was created automatically.", "fulfilled", visible_check)
