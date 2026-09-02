import pytest
from pydantic import TypeAdapter, ValidationError

from investigator.cycle import InvestigatorCycleCoordinator
from investigator.cycle_prompt import build_investigator_cycle_prompt
from investigator.graph import CaseGraph, GraphNode, GraphNodeType
from investigator.roles import (
    GraphInvestigationCoordinator,
    InvestigationFocus,
    INVESTIGATOR_UPDATE_ADAPTER,
    StewardReviewContext,
)
from investigator.roles.procedure import (
    INVESTIGATOR_INTENT_COVERAGE,
    INVESTIGATOR_PROCEDURE,
    STEWARD_INTENT_COVERAGE,
    STEWARD_PROCEDURE,
    procedural_contract_errors,
    render_procedure,
)
from investigator.roles.steward import StewardDecision


def _graph() -> CaseGraph:
    return CaseGraph(
        case_id="procedure",
        nodes={
            "U1": GraphNode(id="U1", node_type=GraphNodeType.UNCERTAINTY, statement="What remains unknown?"),
        },
        edges={},
    )


def test_procedure_metadata_covers_every_modeled_intent() -> None:
    assert procedural_contract_errors() == []
    assert {item.operation for item in INVESTIGATOR_PROCEDURE} >= set(INVESTIGATOR_INTENT_COVERAGE.values())
    assert {item.operation for item in STEWARD_PROCEDURE} >= set(STEWARD_INTENT_COVERAGE.values())
    for item in (*INVESTIGATOR_PROCEDURE, *STEWARD_PROCEDURE):
        assert item.role and item.purpose and item.prerequisites and item.rationale and item.valid_alternative


def test_source_evidence_proposition_path_is_explicit_and_legal() -> None:
    coordinator = GraphInvestigationCoordinator(_graph(), InvestigationFocus(node_id="U1"))
    coordinator.apply_investigator_update({
        "operation": "add_evidence",
        "node_id": "E1",
        "statement": "The readable record directly states a marker observation.",
        "source_ids": ["S21"],
        "reason": "This records the direct source observation before inference.",
    })
    coordinator.apply_investigator_update({
        "operation": "add_proposition",
        "node_id": "P1",
        "statement": "The observation supports a smaller claim.",
        "derived_from_node_ids": ["E1"],
        "reason": "The claim is a bounded inference from the recorded observation.",
    })
    assert coordinator.graph.nodes["E1"].metadata["source_ids"] == ["S21"]
    assert "P1_DERIVED_FROM_E1" in coordinator.graph.edges


def test_invalid_source_and_uncertainty_paths_have_procedural_alternatives() -> None:
    coordinator = GraphInvestigationCoordinator(_graph(), InvestigationFocus(node_id="U1"))
    with pytest.raises(ValueError, match="Unknown graph node"):
        coordinator.apply_investigator_update({
            "operation": "add_proposition",
            "node_id": "P1",
            "statement": "bad",
            "derived_from_node_ids": ["S21"],
            "reason": "not legal",
        })
    assert "add_evidence" in render_procedure("investigator")
    assert "request_open" in render_procedure("investigator")
    assert "request_steward_review" in render_procedure("investigator")


def test_investigator_contract_check_requires_canonical_procedure_in_prompt() -> None:
    coordinator = InvestigatorCycleCoordinator(_graph(), InvestigationFocus(node_id="U1"))
    observation = coordinator.observation()
    prompt = build_investigator_cycle_prompt(observation)
    diagnostics = coordinator.contract_check(observation, prompt)
    assert diagnostics["case_revision"] == 0
    with pytest.raises(ValueError, match="procedure"):
        coordinator.contract_check(observation, prompt.replace("<INVESTIGATOR_PROCEDURE>", "<REMOVED>"))


def test_steward_procedure_preserves_handoff_via_stop_contract() -> None:
    assert "human decision-maker" in render_procedure("steward")
    decision = {
        "operation": "stop_unresolved",
        "assessment": "The trusted frontier is exhausted.",
        "reason": "Human review is required for the unresolved questions.",
        "important_unresolved_ids": ["U1"],
        "reopening_conditions": "New material evidence.",
    }
    parsed = TypeAdapter(StewardDecision).validate_python(decision)
    assert parsed.operation == "stop_unresolved"
