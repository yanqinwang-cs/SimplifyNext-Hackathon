import pytest
from pydantic import ValidationError

from experiments.investigation_smoke.case_01.runner import apply_revision, initial_state
from experiments.investigation_smoke.case_01.schemas import (
    HypothesisProposal,
    InitialResponse,
    NewUncertainty,
    RevisionResponse,
)
from tests.test_investigation_smoke import initial_response
from investigator.models import (
    HypothesisTransition,
    HypothesisTransitionType,
    UncertaintyTransition,
    UncertaintyTransitionType,
)
from investigator.state import CaseState


def proposal(identifier: str = "H1") -> HypothesisProposal:
    return HypothesisProposal(
        id=identifier,
        statement="A broad explanation.",
        supported_by=["E1"],
        conflicted_by=[],
        unresolved=["An unresolved question."],
        specificity_basis=[],
    )


def test_model_generated_identifiers_are_structured_and_text_stays_separate() -> None:
    assert proposal("H2.1").supported_by == ["E1"]
    assert proposal("H2.1").parent_id is None
    for field, value in (("id", "H1 - broad explanation"), ("supported_by", ["E1: observation"]), ("specificity_basis", ["A1"])):
        values = proposal().model_dump()
        values[field] = value
        with pytest.raises(ValidationError):
            HypothesisProposal.model_validate(values)
    with pytest.raises(ValidationError):
        InitialResponse.model_validate({
            "hypotheses": [proposal().model_dump()], "selected_action_id": "A1 — verify",
            "target_uncertainty": "x", "expected_information_value": "x", "why_this_action_now": "x",
        })


def test_shared_transition_identifier_boundaries() -> None:
    with pytest.raises(ValidationError):
        HypothesisTransition(hypothesis_id="H1 - explanation", transition="keep", reason="x")
    with pytest.raises(ValidationError):
        HypothesisTransition(hypothesis_id="H1", transition="keep", reason="x", add_supporting_evidence_ids=["A1"])
    with pytest.raises(ValidationError):
        UncertaintyTransition(uncertainty_id="H1: unresolved", transition="keep", reason="x")


def test_duplicate_initial_hypothesis_ids_are_rejected_before_state_construction() -> None:
    response = initial_response()
    response.hypotheses.append(response.hypotheses[0].model_copy(deep=True))
    with pytest.raises(ValueError, match="Duplicate hypothesis ID"):
        initial_state(response)


def test_uncertainty_updates_resolve_and_add_structured_uncertainties() -> None:
    state = initial_state(initial_response())
    response = RevisionResponse(
        hypothesis_updates=[],
        new_hypotheses=[],
        uncertainty_updates=[UncertaintyTransition(
            uncertainty_id="H1:U1", transition=UncertaintyTransitionType.RESOLVE, reason="Release resolves it."
        )],
        new_uncertainties=[NewUncertainty(
            id="H1:U9", hypothesis_id="H1", description="A newly observed uncertainty."
        )],
        revision_rationale="The uncertainty set changed.",
    )
    updated = apply_revision(state, response)
    assert "H1:U1" not in updated.uncertainties
    assert "H1:U1" not in updated.get_hypothesis("H1").unresolved_issue_ids
    assert "H1:U9" in updated.uncertainties
    assert "H1:U9" in updated.get_hypothesis("H1").unresolved_issue_ids


def test_state_referential_integrity_rejects_missing_source_and_uncertainty() -> None:
    with pytest.raises(ValueError, match="Unknown source"):
        CaseState.model_validate({
            "case_id": "case", "title": "x",
            "evidence": {"E1": {"id": "E1", "source_id": "missing", "raw_content": "x", "kind": "other"}},
        })
    state = initial_state(initial_response())
    state.hypotheses["H1"].unresolved_issue_ids.append("H1:U99")
    with pytest.raises(ValueError, match="Unknown uncertainty"):
        CaseState.model_validate(state.model_dump())
