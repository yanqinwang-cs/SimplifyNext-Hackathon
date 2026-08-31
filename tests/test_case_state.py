from investigator.models import (
    ApproximateTime, Claim, ClaimStatus, EvidenceItem, EvidenceKind,
    Hypothesis, HypothesisOrigin, HypothesisStatus, Source, SourceType,
    HypothesisTransformation, TransformationType, Uncertainty, UncertaintyKind,
)
from investigator.state import CaseState


def test_constructing_case_and_epistemic_distinctions() -> None:
    state = CaseState(case_id="case-1", title="Example")
    source = Source(id="s1", name="Witness", source_type=SourceType.PERSON)
    evidence = EvidenceItem(id="e1", source_id="s1", raw_content="I saw it.", kind=EvidenceKind.STATEMENT)
    claim = Claim(id="c1", proposition="The witness says they saw it.", asserted_by_source_id="s1")
    state.sources[source.id] = source
    state.evidence[evidence.id] = evidence
    state.claims[claim.id] = claim

    assert state.sources["s1"] is source
    assert state.evidence["e1"].raw_content == "I saw it."
    assert state.claims["c1"].status is ClaimStatus.ASSERTED
    assert not hasattr(state, "facts")


def test_witness_statement_is_not_automatically_a_fact() -> None:
    claim = Claim(id="c1", proposition="Alice controlled Account X")
    assert claim.proposition == "Alice controlled Account X"
    assert not hasattr(claim, "truth")
    assert not hasattr(claim, "confidence")


def test_hypothesis_has_independent_support_and_conflict() -> None:
    hypothesis = Hypothesis(
        id="h1", statement="Alice controlled Account X", origin=HypothesisOrigin.HUMAN,
        supporting_evidence_ids=["e3"], conflicting_evidence_ids=["e2"],
    )
    assert hypothesis.status is HypothesisStatus.PROPOSED
    assert hypothesis.supporting_evidence_ids == ["e3"]
    assert hypothesis.conflicting_evidence_ids == ["e2"]


def test_approximate_time_preserves_raw_language() -> None:
    time = ApproximateTime(raw_expression="around ten")
    assert time.model_dump() == {"kind": "approximate", "raw_expression": "around ten"}


def test_hypothesis_transformation_relationship_is_preserved() -> None:
    transformation = HypothesisTransformation(
        parent_hypothesis_id="h1", child_hypothesis_id="h2",
        transformation_type=TransformationType.NARROWING,
    )
    state = CaseState(case_id="case-1", title="Example", transformations=[transformation])
    assert state.model_copy(deep=True).transformations[0] == transformation
