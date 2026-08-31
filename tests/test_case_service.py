import pytest
from investigator.models import (
    Claim, Conflict, EvidenceItem, EvidenceKind, Hypothesis, HypothesisOrigin,
    Source, SourceType, Uncertainty, UncertaintyKind,
)
from investigator.services import CaseService
from investigator.state import CaseRepository


def test_service_revisions_and_reference_validation(tmp_path) -> None:
    service = CaseService(CaseRepository(tmp_path / "cases"))
    state = service.create_case("case-1", "Test")
    assert state.revision == 0
    service.add_source("case-1", Source(id="s1", name="Source", source_type=SourceType.PERSON))
    assert service.repository.load("case-1").revision == 1
    service.add_evidence("case-1", EvidenceItem(id="e1", source_id="s1", raw_content="raw", kind=EvidenceKind.STATEMENT))
    service.add_hypothesis("case-1", Hypothesis(id="h1", statement="Maybe", origin=HypothesisOrigin.HUMAN))
    service.link_supporting_evidence("case-1", "h1", "e1")
    service.link_conflicting_evidence("case-1", "h1", "e1")
    service.change_hypothesis_status("case-1", "h1", "active")
    assert service.repository.load("case-1").revision == 6

    with pytest.raises(ValueError, match="Duplicate ID"):
        service.add_source("case-1", Source(id="s1", name="Again", source_type=SourceType.OTHER))
    with pytest.raises(KeyError, match="Unknown source ID"):
        service.add_evidence("case-1", EvidenceItem(id="e2", source_id="missing", raw_content="raw", kind=EvidenceKind.OTHER))


def test_end_to_end_investigation_state_survives_reload(tmp_path) -> None:
    repository = CaseRepository(tmp_path / "cases")
    service = CaseService(repository)
    service.create_case("bank-case", "Account X")
    service.add_source("bank-case", Source(id="bank", name="Bank", source_type=SourceType.DOCUMENT))
    service.add_source("bank-case", Source(id="alice", name="Alice", source_type=SourceType.PERSON))
    service.add_source("bank-case", Source(id="bob", name="Bob", source_type=SourceType.PERSON))
    service.add_evidence("bank-case", EvidenceItem(id="E1", source_id="bank", raw_content="bank transaction record", kind=EvidenceKind.RECORD))
    service.add_evidence("bank-case", EvidenceItem(id="E2", source_id="alice", raw_content="Alice says she did not control Account X", kind=EvidenceKind.STATEMENT))
    service.add_evidence("bank-case", EvidenceItem(id="E3", source_id="bob", raw_content="Bob says Alice controlled Account X", kind=EvidenceKind.STATEMENT))
    service.add_claim("bank-case", Claim(id="C1", proposition="Alice claims she did not control X", asserted_by_source_id="alice", evidence_ids=["E2"]))
    service.add_claim("bank-case", Claim(id="C2", proposition="Bob claims Alice controlled X", asserted_by_source_id="bob", evidence_ids=["E3"]))
    service.add_uncertainty("bank-case", Uncertainty(id="U1", kind=UncertaintyKind.UNRESOLVED_IDENTITY, description="Actual operator of Account X remains unresolved"))
    service.add_hypothesis("bank-case", Hypothesis(id="H1", statement="Alice controlled Account X", origin=HypothesisOrigin.HUMAN, unresolved_issue_ids=["U1"]))
    service.link_supporting_evidence("bank-case", "H1", "E3")
    service.link_conflicting_evidence("bank-case", "H1", "E2")
    service.add_conflict("bank-case", Conflict(id="conflict-1", description="Alice and Bob disagree about control of Account X", claim_ids=["C1", "C2"], evidence_ids=["E2", "E3"]))

    loaded = repository.load("bank-case")
    assert loaded.evidence["E2"].raw_content.startswith("Alice says")
    assert loaded.claims["C1"].evidence_ids == ["E2"]
    assert loaded.hypotheses["H1"].supporting_evidence_ids == ["E3"]
    assert loaded.hypotheses["H1"].conflicting_evidence_ids == ["E2"]
    assert loaded.uncertainties["U1"].kind is UncertaintyKind.UNRESOLVED_IDENTITY
    assert loaded.conflicts["conflict-1"].resolved is False

