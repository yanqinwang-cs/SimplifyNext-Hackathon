import pytest
from pydantic import ValidationError

from investigator.models import (
    EvidenceItem, EvidenceKind, Hypothesis, HypothesisOrigin, HypothesisStatus,
    HypothesisTransition, HypothesisTransitionType, Source, SourceType,
)
from investigator.services import CaseService
from investigator.state import CaseRepository, CaseState, apply_hypothesis_updates


def evidence(evidence_id: str) -> EvidenceItem:
    return EvidenceItem(id=evidence_id, source_id="source", raw_content="observation", kind=EvidenceKind.OBSERVATION)


def tree() -> CaseState:
    return CaseState(
        case_id="case", title="Tree",
        sources={"source": Source(id="source", name="Source", source_type=SourceType.OTHER)},
        evidence={"E1": evidence("E1"), "E2": evidence("E2")},
        hypotheses={
            "H1": Hypothesis(id="H1", statement="Broad explanation", origin=HypothesisOrigin.HUMAN, status=HypothesisStatus.ACTIVE, supporting_evidence_ids=["E1"]),
            "H2": Hypothesis(id="H2", statement="Another root", origin=HypothesisOrigin.HUMAN),
            "H1.1": Hypothesis(id="H1.1", parent_id="H1", statement="Narrow explanation", origin=HypothesisOrigin.HUMAN, status=HypothesisStatus.ACTIVE, specificity_basis=["E2"]),
        },
    )


def test_roots_children_and_parent_child_ancestry() -> None:
    state = tree()
    assert [h.id for h in state.root_hypotheses()] == ["H1", "H2"]
    assert [h.id for h in state.children_of("H1")] == ["H1.1"]
    assert state.get_evidence("E1").id == "E1"
    assert state.get_hypothesis("H1.1").parent_id == "H1"


def test_updates_remove_or_weaken_child_without_mutating_parent_or_input() -> None:
    state = tree()
    updated = apply_hypothesis_updates(state, [
        HypothesisTransition(hypothesis_id="H1.1", transition=HypothesisTransitionType.REMOVE, reason="New evidence conflicts with the narrow mechanism."),
    ])
    assert updated.get_hypothesis("H1.1").status is HypothesisStatus.REMOVED
    assert updated.get_hypothesis("H1").status is HypothesisStatus.ACTIVE
    assert state.get_hypothesis("H1.1").status is HypothesisStatus.ACTIVE
    assert state.get_hypothesis("H1").statement == updated.get_hypothesis("H1").statement
    assert updated.get_evidence("E1").raw_content == "observation"

    weakened = apply_hypothesis_updates(state, [
        HypothesisTransition(hypothesis_id="H1.1", transition=HypothesisTransitionType.WEAKEN, reason="Less specific than before."),
    ])
    assert weakened.get_hypothesis("H1.1").status is HypothesisStatus.WEAKENED
    assert weakened.get_hypothesis("H1").status is HypothesisStatus.ACTIVE


def test_updates_add_evidence_provenance_without_replacing_existing_references() -> None:
    state = tree()
    updated = apply_hypothesis_updates(state, [HypothesisTransition(
        hypothesis_id="H1",
        transition=HypothesisTransitionType.KEEP,
        reason="New evidence is relevant.",
        add_supporting_evidence_ids=["E2", "E2"],
        add_conflicting_evidence_ids=["E2"],
        add_specificity_basis_evidence_ids=["E2"],
    )])
    hypothesis = updated.get_hypothesis("H1")
    assert hypothesis.supporting_evidence_ids == ["E1", "E2"]
    assert hypothesis.conflicting_evidence_ids == ["E2"]
    assert hypothesis.specificity_basis == ["E2"]
    assert state.get_hypothesis("H1").supporting_evidence_ids == ["E1"]


def test_updates_reject_unknown_or_hypothesis_evidence_ids() -> None:
    with pytest.raises(ValidationError, match="pattern"):
        apply_hypothesis_updates(tree(), [HypothesisTransition(
            hypothesis_id="H1", transition="keep", reason="audit",
            add_supporting_evidence_ids=["missing"],
        )])
    with pytest.raises(ValidationError, match="pattern"):
        apply_hypothesis_updates(tree(), [HypothesisTransition(
            hypothesis_id="H1", transition="keep", reason="audit",
            add_specificity_basis_evidence_ids=["H1.1"],
        )])


def test_explicit_parent_activation_and_reason_is_not_evidence() -> None:
    state = tree()
    state.hypotheses["H1"].status = HypothesisStatus.WEAKENED
    updated = apply_hypothesis_updates(state, [
        HypothesisTransition(hypothesis_id="H1", transition="activate", reason="The broader explanation remains viable."),
    ])
    assert updated.get_hypothesis("H1").status is HypothesisStatus.ACTIVE
    assert all("broader explanation" not in item.raw_content for item in updated.evidence.values())


@pytest.mark.parametrize("bad_hypotheses, message", [
    ({"H1": Hypothesis(id="H1", parent_id="missing", statement="x", origin=HypothesisOrigin.HUMAN)}, "Unknown parent"),
    ({"H1": Hypothesis(id="H1", parent_id="H1", statement="x", origin=HypothesisOrigin.HUMAN)}, "own parent"),
    ({"H1": Hypothesis(id="H1", parent_id="H2", statement="x", origin=HypothesisOrigin.HUMAN), "H2": Hypothesis(id="H2", parent_id="H1", statement="y", origin=HypothesisOrigin.HUMAN)}, "cycle"),
    ({"H1": Hypothesis(id="H1", statement="x", origin=HypothesisOrigin.HUMAN, supporting_evidence_ids=["missing"])}, "evidence"),
])
def test_invalid_tree_structure_rejected(bad_hypotheses, message: str) -> None:
    with pytest.raises((ValidationError, ValueError), match=message):
        CaseState(
            case_id="case", title="Bad",
            sources={"source": Source(id="source", name="Source", source_type=SourceType.OTHER)},
            evidence={"E1": evidence("E1")}, hypotheses=bad_hypotheses,
        )


@pytest.mark.parametrize("field", ["supporting_evidence_ids", "conflicting_evidence_ids", "specificity_basis"])
def test_missing_evidence_reference_rejected(field: str) -> None:
    hypothesis = Hypothesis(id="H1", statement="x", origin=HypothesisOrigin.HUMAN)
    setattr(hypothesis, field, ["missing"])
    with pytest.raises(ValueError, match="Unknown evidence"):
        CaseState(case_id="case", title="Bad", hypotheses={"H1": hypothesis})


def test_hypothesis_id_cannot_be_used_as_evidence_reference() -> None:
    with pytest.raises(ValueError, match="cannot be used as evidence"):
        CaseState(case_id="case", title="Bad", hypotheses={
            "H1": Hypothesis(id="H1", statement="x", origin=HypothesisOrigin.HUMAN, supporting_evidence_ids=["H1"]),
        })


def test_service_rejects_duplicate_ids_and_adds_child(tmp_path) -> None:
    service = CaseService(CaseRepository(tmp_path / "cases"))
    service.create_case("case", "Tree")
    service.add_source("case", Source(id="source", name="Source", source_type=SourceType.OTHER))
    service.add_evidence("case", evidence("E1"))
    service.add_hypothesis("case", Hypothesis(id="H1", statement="Broad", origin=HypothesisOrigin.HUMAN))
    with pytest.raises(ValueError, match="Duplicate ID"):
        service.add_evidence("case", evidence("E1"))
    service.add_hypothesis("case", Hypothesis(id="H1.1", parent_id="H1", statement="Narrow", origin=HypothesisOrigin.HUMAN))
    with pytest.raises(ValueError, match="Duplicate ID"):
        service.add_hypothesis("case", Hypothesis(id="H1", statement="Duplicate", origin=HypothesisOrigin.HUMAN))
    assert service.repository.load("case").get_hypothesis("H1.1").parent_id == "H1"


def test_evidence_and_hypothesis_ids_are_frozen() -> None:
    item = evidence("E1")
    hypothesis = Hypothesis(id="H1", statement="x", origin=HypothesisOrigin.HUMAN)
    with pytest.raises(ValidationError):
        item.id = "E2"
    with pytest.raises(ValidationError):
        hypothesis.id = "H2"
