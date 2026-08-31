from investigator.models import (
    Claim, EvidenceItem, EvidenceKind, Hypothesis, HypothesisOrigin, Source, SourceType,
)
from investigator.state import CaseRepository, CaseState


def test_json_save_load_reproduces_complete_state(tmp_path) -> None:
    repository = CaseRepository(tmp_path / "cases")
    state = CaseState(case_id="case-1", title="Stored", revision=4)
    state.sources["s1"] = Source(id="s1", name="Bank", source_type=SourceType.DATABASE)
    state.evidence["e1"] = EvidenceItem(id="e1", source_id="s1", raw_content="record", kind=EvidenceKind.RECORD)
    state.claims["c1"] = Claim(id="c1", proposition="A claim", evidence_ids=["e1"])
    state.hypotheses["h1"] = Hypothesis(
        id="h1", statement="A hypothesis", origin=HypothesisOrigin.AGENT_SUGGESTION,
        supporting_evidence_ids=["e1"],
    )
    repository.save(state)

    loaded = repository.load("case-1")
    assert loaded == state
    assert repository.exists("case-1")
    assert repository.list_case_ids() == ["case-1"]

