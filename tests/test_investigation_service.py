import pytest

from experiments.investigation_smoke.case_01.schemas import NextActionResponse, RevisionResponse
from tests.test_investigation_smoke import call_result, initial_response
from investigator.services import InvestigationService, InvalidSessionTransition, SessionStatus


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def call(self, prompt, schema):
        self.calls.append((prompt, schema))
        return next(self.responses)


def revision_result():
    return call_result(RevisionResponse(
        hypothesis_updates=[], new_hypotheses=[], uncertainty_updates=[], new_uncertainties=[], revision_rationale="No structural change.",
    ))


def action_result(action_id: str):
    return call_result(NextActionResponse(
        selected_action_id=action_id, target_uncertainty="An open question.",
        expected_information_value="It may distinguish explanations.", why_this_action_now="It is available and useful.",
    ))


def test_session_pauses_at_human_action_review_without_release() -> None:
    service = InvestigationService(FakeClient([call_result(initial_response("A1"))]))
    session = service.start_case()
    assert session.status is SessionStatus.AWAITING_ACTION_REVIEW
    assert session.pending_action.action_id == "A1"
    assert set(session.case_state.evidence) == {f"E{i}" for i in range(1, 8)}


def test_redirect_is_pending_and_execute_releases_only_redirected_action() -> None:
    service = InvestigationService(FakeClient([call_result(initial_response("A1"))]))
    session = service.start_case()
    service.set_human_action(session, "A3", "Behaviour is more consequential.")
    assert session.pending_action.action_id == "A3"
    assert "A3_RELEASE" not in session.case_state.evidence
    service.execute_action(session)
    assert "A3_RELEASE" in session.case_state.evidence
    assert session.completed_action_ids == {"A3"}
    assert session.case_state.revision == 0


def test_illegal_transitions_and_completed_actions_are_rejected() -> None:
    service = InvestigationService(FakeClient([call_result(initial_response("A1"))]))
    session = service.start_case()
    with pytest.raises(InvalidSessionTransition):
        service.apply_revision(session)
    service.execute_action(session)
    with pytest.raises(InvalidSessionTransition):
        service.execute_action(session)
    service.stop(session)
    with pytest.raises(InvalidSessionTransition):
        service.propose_revision(session)


def test_two_complete_cycles_are_repeatable_and_exclude_completed_actions() -> None:
    client = FakeClient([
        call_result(initial_response("A1")), revision_result(), action_result("A2"), revision_result(),
    ])
    service = InvestigationService(client)
    session = service.start_case()
    service.execute_action(session)
    service.propose_revision(session)
    assert session.case_state.hypotheses["H1"].statement
    service.apply_revision(session)
    assert session.status is SessionStatus.READY
    assert session.case_state.revision == 1
    service.propose_next_action(session)
    assert session.pending_action.action_id == "A2"
    assert '"action_id": "A1"' not in client.calls[-1][0].split("Available enquiries:", 1)[1]
    service.execute_action(session)
    service.propose_revision(session)
    service.apply_revision(session)
    assert session.case_state.revision == 2
    assert session.completed_action_ids == {"A1", "A2"}


def test_stopped_session_cannot_silently_continue() -> None:
    service = InvestigationService(FakeClient([call_result(initial_response("A1"))]))
    session = service.start_case()
    service.stop(session)
    with pytest.raises(InvalidSessionTransition):
        service.set_human_action(session, "A2")
