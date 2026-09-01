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
from investigator.llm.base import ModelCallMetadata, ModelCallResult
from experiments.investigation_smoke.case_01.runner import run
from experiments.investigation_smoke.case_01.schemas import ControlledRunTrace
from experiments.investigation_smoke.context import render_assessment_context
from experiments.investigation_smoke.case_01.prompts import initial_prompt, revision_prompt


def proposal(identifier: str = "H1") -> HypothesisProposal:
    return HypothesisProposal(
        id=identifier,
        statement="A broad explanation.",
        supported_by=["E1"],
        conflicted_by=[],
        unresolved=["An unresolved question."],
        specificity_basis_evidence_ids=[],
    )


def test_model_generated_identifiers_are_structured_and_text_stays_separate() -> None:
    assert proposal("H2.1").supported_by == ["E1"]
    assert proposal("H2.1").parent_id is None
    for field, value in (("id", "H1 - broad explanation"), ("supported_by", ["E1: observation"]), ("specificity_basis_evidence_ids", ["A1"])):
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


def test_other_operations_require_details_and_are_forbidden_for_normal_transitions() -> None:
    with pytest.raises(ValidationError, match="requires"):
        HypothesisTransition(hypothesis_id="H1", transition="other", reason="x")
    with pytest.raises(ValidationError, match="OTHER-only"):
        HypothesisTransition(hypothesis_id="H1", transition="keep", reason="x", requested_effect="split")
    with pytest.raises(ValidationError, match="requires"):
        UncertaintyTransition(uncertainty_id="U1", transition="other", reason="x")
    with pytest.raises(ValidationError, match="requires new_description"):
        UncertaintyTransition(uncertainty_id="U1", transition="refine", reason="x")


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
            uncertainty_id="U1", transition=UncertaintyTransitionType.RESOLVE, reason="Release resolves it."
        )],
        new_uncertainties=[NewUncertainty(
            id="U9", hypothesis_id="H1", description="A newly observed uncertainty."
        )],
        revision_rationale="The uncertainty set changed.",
    )
    updated = apply_revision(state, response)
    assert "U1" not in updated.uncertainties
    assert "U1" not in updated.get_hypothesis("H1").unresolved_issue_ids
    assert "U9" in updated.uncertainties
    assert "U9" in updated.get_hypothesis("H1").unresolved_issue_ids


def test_refine_updates_description_and_other_preserves_state() -> None:
    state = initial_state(initial_response())
    refined = apply_revision(state, RevisionResponse(
        hypothesis_updates=[], new_hypotheses=[],
        uncertainty_updates=[UncertaintyTransition(
            uncertainty_id="U1", transition="refine", reason="Evidence narrows the question.",
            new_description="A more precise question.", basis_evidence_ids=["E1"],
        )], new_uncertainties=[], revision_rationale="refined",
    ))
    assert refined.uncertainties["U1"].description == "A more precise question."
    assert refined.uncertainties["U1"].evidence_ids == ["E1"]
    assert state.uncertainties["U1"].description != refined.uncertainties["U1"].description

    other = HypothesisTransition(
        hypothesis_id="H1", transition="other", reason="Structural change is unsupported.",
        requested_operation_name="split", requested_effect="Create two children.",
        why_existing_operations_do_not_fit={"keep": "Not faithful."},
    )
    unchanged = apply_revision(state, RevisionResponse(
        hypothesis_updates=[other], new_hypotheses=[], uncertainty_updates=[], new_uncertainties=[], revision_rationale="other",
    ))
    assert unchanged.model_copy(update={"revision": state.revision}).model_dump() == state.model_dump()
    assert unchanged.revision == state.revision + 1


def test_uncertainty_provenance_is_deduplicated_and_unknown_ids_fail() -> None:
    state = initial_state(initial_response())
    state.uncertainties["U1"].evidence_ids = ["E1"]
    refined = apply_revision(state, RevisionResponse(
        hypothesis_updates=[], new_hypotheses=[],
        uncertainty_updates=[UncertaintyTransition(
            uncertainty_id="U1", transition="keep", reason="Same basis remains relevant.", basis_evidence_ids=["E1", "E2"],
        )], new_uncertainties=[], revision_rationale="kept",
    ))
    assert refined.uncertainties["U1"].evidence_ids == ["E1", "E2"]
    with pytest.raises(KeyError, match="Unknown evidence"):
        apply_revision(state, RevisionResponse(
            hypothesis_updates=[], new_hypotheses=[],
            uncertainty_updates=[UncertaintyTransition(
                uncertainty_id="U1", transition="keep", reason="bad", basis_evidence_ids=["E99"],
            )], new_uncertainties=[], revision_rationale="bad",
        ))


def test_other_operation_is_preserved_in_trace_without_state_mutation(tmp_path) -> None:
    class Client:
        def __init__(self):
            self.calls = 0

        def call(self, prompt, schema):
            self.calls += 1
            if self.calls == 1:
                return ModelCallResult(
                    parsed=initial_response("A1"),
                    metadata=ModelCallMetadata(provider="mock", model="fixture", latency_seconds=0, parse_success=True),
                )
            return ModelCallResult(
                parsed=RevisionResponse(
                    hypothesis_updates=[HypothesisTransition(
                        hypothesis_id="H1", transition="other", reason="No defined operation fits.",
                        requested_operation_name="split", requested_effect="Create children.",
                        why_existing_operations_do_not_fit={"keep": "Not structural."},
                    )], new_hypotheses=[], uncertainty_updates=[], new_uncertainties=[], revision_rationale="audit",
                ),
                metadata=ModelCallMetadata(provider="mock", model="fixture", latency_seconds=0, parse_success=True),
            )

    trace = ControlledRunTrace.model_validate_json(run("fixture", tmp_path, client=Client()).read_text())
    assert trace.parse_success is True
    assert trace.unsupported_operations[0]["requested_operation_name"] == "split"


def test_state_referential_integrity_rejects_missing_source_and_uncertainty() -> None:
    with pytest.raises(ValueError, match="Unknown source"):
        CaseState.model_validate({
            "case_id": "case", "title": "x",
            "evidence": {"E1": {"id": "E1", "source_id": "missing", "raw_content": "x", "kind": "other"}},
        })
    state = initial_state(initial_response())
    state.hypotheses["H1"].unresolved_issue_ids.append("U99")
    with pytest.raises(ValueError, match="Unknown uncertainty"):
        CaseState.model_validate(state.model_dump())


def test_prompt_layers_and_case_context_keep_policy_separate_from_evidence() -> None:
    context = render_assessment_context()
    assert "ordinary tutoring and preparation before the examination" in context
    assert "External assistance or communication during the examination is prohibited" in context
    assert "Phone / smartwatch" in context
    assert "not evidence" in context
    assert "RESOURCE_DEVICE" not in initial_state(initial_response()).evidence
    with pytest.raises(ValidationError):
        values = proposal().model_dump()
        values["supported_by"] = ["RESOURCE_TUTOR"]
        HypothesisProposal.model_validate(values)
    prompt = initial_prompt()
    assert prompt.index("GLOBAL INVESTIGATION RULES") < prompt.index("ASSESSMENT ONTOLOGY / POLICY") < prompt.index("CURRENT CASE EVIDENCE / PRIOR STATE")
    assert "CANONICAL JSON OUTPUT TEMPLATE" in prompt
    revision = revision_prompt("EVIDENCE", [], [], {"action_id": "A1"}, "A1_RELEASE", "release")
    assert revision.index("ASSESSMENT ONTOLOGY / POLICY") < revision.index("CURRENT CASE EVIDENCE / PRIOR STATE")
    assert "TASK-SPECIFIC INSTRUCTION" in revision
    assert "A1_RELEASE" in revision
