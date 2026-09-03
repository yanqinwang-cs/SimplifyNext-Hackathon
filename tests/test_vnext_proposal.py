import pytest
from pydantic import ValidationError

from investigator.vnext import InvestigatorProposal


def test_empty_proposal_is_structurally_valid() -> None:
    assert InvestigatorProposal.model_validate({"graph_updates": []}).graph_updates == []


def test_single_and_multi_operation_proposals_validate() -> None:
    proposal = InvestigatorProposal.model_validate({
        "graph_updates": [
            {"operation": "add_evidence", "statement": "A record says X.", "source_ids": ["S1"], "reason": "Record the observation."},
            {"operation": "add_proposition", "statement": "X may be relevant.", "derived_from_node_ids": ["obs"], "reason": "State the bounded inference."},
            {"operation": "add_uncertainty", "statement": "Whether X is complete.", "target_node_id": "P1", "reason": "Preserve the unresolved question."},
            {"operation": "add_support", "source_node_id": "E1", "target_node_id": "P1", "reason": "The record supports the inference."},
        ]
    })
    assert [item.operation for item in proposal.graph_updates] == ["add_evidence", "add_proposition", "add_uncertainty", "add_support"]


def test_same_turn_local_ref_shape_is_accepted_without_resolution() -> None:
    proposal = InvestigatorProposal.model_validate({
        "graph_updates": [
            {"operation": "add_evidence", "local_ref": "obs", "statement": "A record says X.", "source_ids": ["S1"], "reason": "Record the observation."},
            {"operation": "add_proposition", "statement": "X may be relevant.", "derived_from_node_ids": ["obs"], "reason": "State the bounded inference."},
        ]
    })
    assert proposal.graph_updates[0].local_ref == "obs"
    assert proposal.graph_updates[1].derived_from_node_ids == ["obs"]


@pytest.mark.parametrize("operation", [
    "move_focus", "continue_local", "local_exhausted", "request_information",
    "request_open", "request_evidence", "request_enquiry", "request_steward_review",
    "add_conclusion",
])
def test_non_graph_and_unknown_operations_are_rejected(operation: str) -> None:
    with pytest.raises(ValidationError):
        InvestigatorProposal.model_validate({"graph_updates": [{"operation": operation}]})


def test_next_step_and_unrelated_top_level_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        InvestigatorProposal.model_validate({"graph_updates": [], "next_step": {"type": "continue_local"}})
    with pytest.raises(ValidationError):
        InvestigatorProposal.model_validate({"graph_updates": [], "analysis": "prose"})


@pytest.mark.parametrize("payload", [
    {"operation": "add_evidence", "statement": "Missing provenance.", "reason": "Not enough."},
    {"operation": "add_proposition", "derived_from_node_ids": ["E1"], "reason": "Missing statement."},
    {"operation": "add_support", "source_node_id": "E1", "reason": "Missing target."},
])
def test_reused_operation_required_fields_remain_strict(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        InvestigatorProposal.model_validate({"graph_updates": [payload]})


def test_generated_schema_contains_only_vnext_graph_operation_tags() -> None:
    schema = InvestigatorProposal.model_json_schema()
    tags = {
        branch["properties"]["operation"]["const"]
        for branch in schema["$defs"].values()
        if isinstance(branch, dict) and "properties" in branch and "operation" in branch["properties"]
    }
    assert tags == {
        "add_evidence", "add_proposition", "add_hypothesis", "add_uncertainty",
        "add_support", "add_conflict", "add_derivation", "add_specialization",
    }
