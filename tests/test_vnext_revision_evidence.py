from pathlib import Path

from investigator.graph import GraphNodeType
from investigator.state import CaseRepository
from investigator.vnext import (
    AssessmentStatus,
    Confidence,
    FurthestJustifiedConclusion,
    InvestigatorAssessment,
    InvestigatorProposal,
    VNextInvestigationRunner,
    run_input_from_case_state,
    ViolationAssessment,
)
from investigator.vnext.presets import academic_integrity_core_preset


LEGACY_CASE_FIXTURE = Path(__file__).parent / "fixtures" / "legacy" / "case-01.json"


def _assessment(source_ids: list[str]) -> InvestigatorAssessment:
    return InvestigatorAssessment(
        proposal=InvestigatorProposal(),
        violation_assessments=[
            ViolationAssessment(
                violation_id=rule.violation_id,
                status=AssessmentStatus.NOT_CURRENTLY_SUPPORTED,
                supporting_node_ids=[],
                mitigating_node_ids=[],
                unresolved_points=[f"Further review of {source_ids[-1]} may be needed."],
                reasoning_summary="Fixture assessment only.",
                confidence=Confidence.LOW,
            )
            for rule in academic_integrity_core_preset().violations
        ],
        furthest_conclusion=FurthestJustifiedConclusion(
            statement="The fixture remains bounded.",
            based_on_violation_ids=[],
            confidence=Confidence.LOW,
        ),
    )


def test_case_01_preserves_original_non_observation_and_adds_revision_sources() -> None:
    state = CaseRepository(LEGACY_CASE_FIXTURE.parent).load(LEGACY_CASE_FIXTURE.stem)

    assert "I did not hear Candidate A speaking" in state.sources["S29"].content
    assert "S30" in state.sources
    assert "same pair worn by Candidate A" in state.sources["S30"].content
    assert "smart glasses" in state.sources["S30"].content
    assert "electronic display and communication capability" in state.sources["S30"].content
    assert "S31" in state.sources
    assert "Student B" in state.sources["S31"].content
    assert "heard Candidate A speaking" in state.sources["S31"].content
    assert "S32" in state.sources
    assert "attending to another part of the room" in state.sources["S32"].content


def test_clean_vnext_input_exposes_all_revision_sources_without_prior_graph() -> None:
    state = CaseRepository(LEGACY_CASE_FIXTURE.parent).load(LEGACY_CASE_FIXTURE.stem)
    inputs = run_input_from_case_state(state, academic_integrity_core_preset())

    assert {"S23", "S29", "S30", "S31", "S32"}.issubset(inputs.sources)
    assert state.reasoning_graph is not None
    result = VNextInvestigationRunner(lambda run_input: _assessment(sorted(run_input.sources))).run(inputs)
    assert set(result.graph.nodes) == set(inputs.sources)
    assert all(node.node_type is GraphNodeType.SOURCE for node in result.graph.nodes.values())


def test_revision_evidence_remains_raw_sources_not_seeded_graph_conclusions() -> None:
    state = CaseRepository(LEGACY_CASE_FIXTURE.parent).load(LEGACY_CASE_FIXTURE.stem)
    inputs = run_input_from_case_state(state, academic_integrity_core_preset())

    assert not inputs.human_inputs
    assert all(source_id in inputs.sources for source_id in ("S30", "S31", "S32"))
    assert state.evidence == {}
