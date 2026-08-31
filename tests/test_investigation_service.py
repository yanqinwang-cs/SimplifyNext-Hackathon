import pytest
from pathlib import Path
import ast
from investigator.environments.case_01 import Case1ControlledEnvironment

from experiments.investigation_smoke.case_01.schemas import NextActionResponse, RevisionResponse
from tests.test_investigation_smoke import call_result, initial_response
from investigator.services import InvestigationService, InvalidSessionTransition, ModelStructuredOutputError, SessionStatus
from investigator.services.contracts import InitialExpansionHypothesis, InitialExpansionResponse, InitialResponse, SeedHypothesisAnalysis
from investigator.llm.base import ModelCallMetadata, ModelCallResult


def make_service(responses):
    return InvestigationService(
        FakeClient(responses),
        Case1ControlledEnvironment(Path(__file__).resolve().parents[1] / "experiments/investigation_smoke/case_01/artifacts"),
    )


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
    service = make_service([call_result(initial_response("A1"))])
    session = service.start_case()
    assert session.status is SessionStatus.AWAITING_ACTION_REVIEW
    assert session.pending_action.action_id == "A1"
    assert set(session.case_state.evidence) == {f"E{i}" for i in range(1, 8)}
    assert session.environment_id == "case_01_controlled"
    assert "Tutor’s session notes" not in session.case_state.evidence["E7"].raw_content


def test_production_modules_have_no_experiment_imports() -> None:
    root = Path(__file__).resolve().parents[1] / "src/investigator"
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("experiments")
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith("experiments") for alias in node.names)


def test_hidden_artifacts_are_absent_until_their_action_executes() -> None:
    environment = Case1ControlledEnvironment(Path(__file__).resolve().parents[1] / "experiments/investigation_smoke/case_01/artifacts")
    session = make_service([call_result(initial_response("A1"))]).start_case()
    hidden = (environment.assets_root / "A2_exam_event_log.xlsx").name
    assert hidden not in environment.initial_prompt()
    assert all(hidden not in evidence.raw_content for evidence in session.case_state.evidence.values())
    service = make_service([call_result(initial_response("A1"))])
    session = service.start_case()
    service.execute_action(session)
    assert "A1_RELEASE" in session.case_state.evidence
    assert "A2_RELEASE" not in session.case_state.evidence


def test_redirect_is_pending_and_execute_releases_only_redirected_action() -> None:
    service = make_service([call_result(initial_response("A1"))])
    session = service.start_case()
    service.set_human_action(session, "A3", "Behaviour is more consequential.")
    assert session.pending_action.action_id == "A3"
    assert "A3_RELEASE" not in session.case_state.evidence
    service.execute_action(session)
    assert "A3_RELEASE" in session.case_state.evidence
    assert session.completed_action_ids == {"A3"}
    assert session.case_state.revision == 0


def test_illegal_transitions_and_completed_actions_are_rejected() -> None:
    service = make_service([call_result(initial_response("A1"))])
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
    service = InvestigationService(client, Case1ControlledEnvironment(Path(__file__).resolve().parents[1] / "experiments/investigation_smoke/case_01/artifacts"))
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
    service = make_service([call_result(initial_response("A1"))])
    session = service.start_case()
    service.stop(session)
    with pytest.raises(InvalidSessionTransition):
        service.set_human_action(session, "A2")


def test_initial_response_is_active_only_and_requires_unresolved() -> None:
    valid = initial_response("A1").model_dump(mode="json")
    assert InitialResponse.model_validate(valid).hypotheses[0].status.value == "active"
    invalid_status = valid.copy()
    invalid_status["hypotheses"] = [dict(valid["hypotheses"][0], status="inactive")]
    with pytest.raises(ValueError):
        InitialResponse.model_validate(invalid_status)
    empty_unresolved = valid.copy()
    empty_unresolved["hypotheses"] = [dict(valid["hypotheses"][0], unresolved=[])]
    with pytest.raises(ValueError):
        InitialResponse.model_validate(empty_unresolved)


def test_initial_structured_failure_preserves_raw_output_and_does_not_retry() -> None:
    class InvalidInitialClient:
        calls = 0

        def call(self, prompt, schema):
            self.calls += 1
            return ModelCallResult(
                parsed={"hypotheses": [], "selected_action_id": "A1"},
                metadata=ModelCallMetadata(provider="mock", model="fixture", latency_seconds=0, parse_success=True),
                raw_output='{"hypotheses": [], "selected_action_id": "A1"}',
            )

    client = InvalidInitialClient()
    service = InvestigationService(
        client,
        Case1ControlledEnvironment(Path(__file__).resolve().parents[1] / "experiments/investigation_smoke/case_01/artifacts"),
    )
    with pytest.raises(ModelStructuredOutputError) as error:
        service.start_case()
    assert client.calls == 1
    assert error.value.raw_output == '{"hypotheses": [], "selected_action_id": "A1"}'


def test_human_seed_becomes_exact_h1_and_model_must_add_competing_root() -> None:
    expansion = InitialExpansionResponse(
        seed_analysis=SeedHypothesisAnalysis(supported_by=["E1"], conflicted_by=[], unresolved=["What remains uncertain?"], specificity_basis_evidence_ids=[]),
        competing_hypotheses=[InitialExpansionHypothesis(
            id="H2", statement="A materially different broad explanation.", status="active", supported_by=["E2"], conflicted_by=[], unresolved=["What else remains uncertain?"], specificity_basis_evidence_ids=[], relationship="competing_root", contrasted_hypothesis_id="H1", material_difference="It differs on the main causal explanation.",
        )], selected_action_id="A1", target_uncertainty="Target", expected_information_value="Value", why_this_action_now="Reason",
    )
    client = FakeClient([call_result(expansion)])
    service = InvestigationService(client, Case1ControlledEnvironment(Path(__file__).resolve().parents[1] / "experiments/investigation_smoke/case_01/artifacts"))
    seed = "The investigator's exact concern."
    session = service.start_case(seed)
    assert session.case_state.hypotheses["H1"].statement == seed
    assert session.case_state.hypotheses["H1"].origin.value == "human_input"
    assert session.case_state.hypotheses["H2"].parent_id is None
    assert client.calls[0][1] is InitialExpansionResponse


def test_competing_root_and_specialization_relationships_are_structurally_strict() -> None:
    with pytest.raises(ValueError):
        InitialExpansionHypothesis(id="H2", parent_id="H1", statement="x", status="active", supported_by=[], conflicted_by=[], unresolved=["u"], specificity_basis_evidence_ids=[], relationship="competing_root", contrasted_hypothesis_id="H1", material_difference="different")
    with pytest.raises(ValueError):
        InitialExpansionHypothesis(id="H2", statement="x", status="active", supported_by=[], conflicted_by=[], unresolved=["u"], specificity_basis_evidence_ids=[], relationship="specialization")


def test_evidence_correction_preserves_history_and_pauses() -> None:
    session = make_service([call_result(initial_response("A1"))]).start_case()
    original = session.case_state.evidence["E1"].raw_content
    service = make_service([])
    service.correct_evidence(session, "E1", "Corrected content", "Investigator correction")
    assert session.case_state.evidence["E1"].raw_content == "Corrected content"
    assert session.case_state.evidence_correction_history[0]["previous_content"] == original
    assert session.status is SessionStatus.PAUSED
