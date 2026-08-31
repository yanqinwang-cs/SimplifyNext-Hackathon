import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from experiments.investigation_smoke.case_01.artifacts import render_xlsx
from experiments.investigation_smoke.case_01.catalog import ENQUIRY_CATALOG, artifact_path, get_action
from experiments.investigation_smoke.case_01.runner import (
    apply_revision,
    initial_state,
    release_selected_artifact,
    run,
)
from experiments.investigation_smoke.case_01.prompts import initial_output_template, revision_output_template, revision_prompt
from experiments.investigation_smoke.case_01.schemas import (
    ControlledRunTrace,
    HypothesisProposal,
    InitialResponse,
    RevisionResponse,
)
from experiments.model_screen.cases import get_case
from investigator.llm.base import ModelCallMetadata, ModelCallResult, ModelParseError
from investigator.models import HypothesisStatus, HypothesisTransition, HypothesisTransitionType


def call_result(parsed):
    return ModelCallResult(
        parsed=parsed,
        metadata=ModelCallMetadata(provider="mock", model="fixture", latency_seconds=0.01, parse_success=True),
        raw_output=parsed.model_dump_json(),
    )


class SequenceClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def call(self, prompt, schema):
        self.calls.append((prompt, schema))
        return next(self.responses)


def initial_response(selected_action_id="A3"):
    return InitialResponse(
        hypotheses=[
            HypothesisProposal(
                id="H1", statement="Some explanation may account for the observed result.",
                supported_by=["E1"], conflicted_by=[], unresolved=["The explanation remains uncertain."], specificity_basis_evidence_ids=[],
            ),
            HypothesisProposal(
                id="H1.1", parent_id="H1", statement="A narrower explanation may account for the result.",
                supported_by=["E3"], conflicted_by=[], unresolved=["The narrower explanation remains uncertain."], specificity_basis_evidence_ids=["E3"],
            ),
        ],
        selected_action_id=selected_action_id,
        target_uncertainty="Whether the movement pattern is habitual.",
        expected_information_value="Could distinguish behavioural and assistance explanations.",
        why_this_action_now="It directly addresses a consequential uncertainty.",
    )


def revision_response():
    return RevisionResponse(
        hypothesis_updates=[
            HypothesisTransition(hypothesis_id="H1.1", transition=HypothesisTransitionType.REMOVE, reason="The artefact does not support this narrow child.", add_conflicting_evidence_ids=["A3_RELEASE"]),
            HypothesisTransition(hypothesis_id="H1", transition=HypothesisTransitionType.ACTIVATE, reason="The broad explanation remains viable.", add_supporting_evidence_ids=["A3_RELEASE"]),
        ],
        uncertainty_updates=[],
        new_uncertainties=[],
        revision_rationale="The released statements support a broader behavioural possibility without establishing a mechanism.",
    )


def test_catalogue_has_exactly_four_actions_and_one_artifact_each() -> None:
    assert [action.action_id for action in ENQUIRY_CATALOG] == ["A1", "A2", "A3", "A4"]
    assert len({action.artifact_filename for action in ENQUIRY_CATALOG}) == 4
    assert all(artifact_path(action.action_id).is_file() for action in ENQUIRY_CATALOG)
    with pytest.raises(ValueError, match="Invalid enquiry"):
        get_action("A9")


def test_only_selected_artifact_is_released_and_unreleased_files_stay_out_of_revision_prompt() -> None:
    state = initial_state(initial_response())
    release = release_selected_artifact(state, "A3")
    assert release.artifact_id == "A3_RELEASE"
    assert release.content == artifact_path("A3").read_text(encoding="utf-8")
    assert "A1_tutoring_verification_packet.md" not in release.content
    assert "A4_student_interview_record.md" not in release.content
    assert set(state.evidence) == {"E1", "E2", "E3", "E4", "E5", "E6", "E7", "A3_RELEASE"}


def test_initial_state_rejects_invalid_evidence_and_hypothesis_references() -> None:
    bad = initial_response()
    bad.hypotheses[0].supported_by = ["missing"]
    with pytest.raises(ValueError, match="Unknown evidence"):
        initial_state(bad)
    bad = initial_response()
    bad.hypotheses[0].supported_by = ["A1"]
    with pytest.raises(ValueError, match="Unknown evidence"):
        initial_state(bad)
    bad = initial_response()
    bad.hypotheses[0].supported_by = ["H1"]
    with pytest.raises(ValueError, match="cannot be used as evidence"):
        initial_state(bad)


def test_experiment_schema_rejects_wrong_or_missing_fields() -> None:
    with pytest.raises(ValidationError):
        HypothesisProposal(
            id="H1", statement="x", supported_by=[], conflicted_by=[], unresolved=[], specificity_basis_evidence_ids=[],
            unresolved_uncertainty="wrong field",
        )
    with pytest.raises(ValidationError):
        HypothesisProposal(id="H1", statement="x", supported_by=[], conflicted_by=[], specificity_basis_evidence_ids=[])


def test_initial_prompt_has_exact_json_contract() -> None:
    from experiments.investigation_smoke.case_01.prompts import initial_prompt

    prompt = initial_prompt()
    assert '"unresolved"' in prompt
    assert '"specificity_basis_evidence_ids"' in prompt
    assert '"selected_action_id"' in prompt
    assert "do not rename unresolved" in prompt.lower()


def test_canonical_prompt_templates_validate_against_response_models() -> None:
    assert InitialResponse.model_validate(initial_output_template())
    assert RevisionResponse.model_validate(revision_output_template("A1_RELEASE"))


def test_revision_schema_requires_reason_and_accepts_valid_response() -> None:
    with pytest.raises(ValidationError):
        RevisionResponse.model_validate({
            "hypothesis_updates": [{
                "hypothesis_id": "H1", "transition": "keep",
                "add_supporting_evidence_ids": [], "add_conflicting_evidence_ids": [], "add_specificity_basis_evidence_ids": [],
            }], "new_hypotheses": [], "remaining_uncertainties": [], "revision_rationale": "x",
        })
    assert revision_response().hypothesis_updates[0].reason
def test_specificity_basis_and_prior_hypotheses_remain_separate_from_released_evidence() -> None:
    state = initial_state(initial_response())
    release_selected_artifact(state, "A3")
    assert state.get_hypothesis("H1.1").specificity_basis == ["E3"]
    assert "H1" not in state.evidence
    assert state.get_hypothesis("H1").statement not in state.get_evidence("A3_RELEASE").raw_content


def test_a2_rendering_is_deterministic_and_complete() -> None:
    path = artifact_path("A2")
    first = render_xlsx(path)
    second = render_xlsx(path)
    assert first == second
    assert "SHEET: Event Log" in first
    assert "Question\tTopic\tDifficulty band" in first
    assert "40\tOdds ratio" in first
    assert "SHEET: Review Log" in first
    assert "General scan" in first


def test_two_call_run_trace_applies_updates_and_round_trips(tmp_path: Path) -> None:
    client = SequenceClient([call_result(initial_response()), call_result(revision_response())])
    trace_path = run("fixture-model", tmp_path, client=client)
    assert len(client.calls) == 2
    trace = ControlledRunTrace.model_validate_json(trace_path.read_text(encoding="utf-8"))
    assert trace.parse_success is True
    assert set(trace.initial_hypothesis_state.evidence) == {"E1", "E2", "E3", "E4", "E5", "E6", "E7"}
    assert trace.selected_action_id == "A3"
    assert trace.release.artifact_id == "A3_RELEASE"
    assert trace.release.content == artifact_path("A3").read_text(encoding="utf-8")
    assert "A1_tutoring_verification_packet.md" not in trace.revision_prompt
    assert trace.final_hypothesis_state.get_hypothesis("H1.1").status is HypothesisStatus.REMOVED
    assert trace.final_hypothesis_state.get_hypothesis("H1").status is HypothesisStatus.ACTIVE
    assert trace.final_hypothesis_state.get_hypothesis("H1").supporting_evidence_ids == ["E1", "A3_RELEASE"]
    assert trace.final_hypothesis_state.get_hypothesis("H1.1").conflicting_evidence_ids == ["A3_RELEASE"]
    assert trace.initial_hypothesis_state.revision == 0
    assert trace.final_hypothesis_state.revision == 1
    assert trace.final_hypothesis_state.get_evidence("A3_RELEASE").raw_content == trace.release.content


def test_revision_is_deterministic_and_preserves_parent() -> None:
    state = initial_state(initial_response())
    release_selected_artifact(state, "A3")
    updated = apply_revision(state, revision_response())
    assert updated.get_hypothesis("H1").status is HypothesisStatus.ACTIVE
    assert updated.get_hypothesis("H1.1").status is HypothesisStatus.REMOVED
    assert state.get_hypothesis("H1").status is HypothesisStatus.ACTIVE


def test_revision_prompt_separates_hypotheses_from_evidence() -> None:
    state = initial_state(initial_response())
    prompt = revision_prompt(
        "original evidence", [h.model_dump(mode="json") for h in state.hypotheses.values()],
        [u.model_dump(mode="json") for u in state.uncertainties.values()],
        {"action_id": "A3"}, "A3_RELEASE", "A3_RELEASE content",
    )
    assert "Prior hypotheses/tree (NOT evidence):" in prompt
    assert "Prior unresolved uncertainties:" in prompt
    assert "Newly released artefact content:" in prompt
    assert '"evidence":' not in prompt
    assert "Selected enquiry/action ID: A3" in prompt
    assert "Newly released evidence ID: A3_RELEASE" in prompt
    assert "action ID" in prompt
    assert "evidence-reference field" in prompt
    assert '"reason"' in prompt
    assert '"add_supporting_evidence_ids"' in prompt
    assert '"add_conflicting_evidence_ids"' in prompt
    assert '"add_specificity_basis_evidence_ids"' in prompt


def test_failed_call_raw_output_is_preserved_in_trace(tmp_path: Path) -> None:
    from experiments.investigation_smoke.case_01.runner import run

    raw_initial = "```json\nnot valid\n```"

    class InitialFailure:
        def call(self, prompt, schema):
            raise ModelParseError("initial schema failure", raw_output=raw_initial)

    first_trace = ControlledRunTrace.model_validate_json(run("model", tmp_path / "first", client=InitialFailure()).read_text())
    assert first_trace.failure_stage == "initial_parse"
    assert first_trace.initial_raw_model_output == raw_initial

    raw_revision = "{malformed revision}"
    class RevisionFailure:
        def __init__(self):
            self.calls = 0

        def call(self, prompt, schema):
            self.calls += 1
            if self.calls == 1:
                return call_result(initial_response("A1"))
            raise ModelParseError("revision schema failure", raw_output=raw_revision)

    second_trace = ControlledRunTrace.model_validate_json(run("model", tmp_path / "second", client=RevisionFailure()).read_text())
    assert second_trace.failure_stage == "revision_parse"
    assert second_trace.revision_raw_model_output == raw_revision
    assert second_trace.initial_hypothesis_state is not None
    assert second_trace.release.artifact_id == "A1_RELEASE"
