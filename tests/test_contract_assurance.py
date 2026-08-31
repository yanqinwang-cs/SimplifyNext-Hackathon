import json
from pathlib import Path

from experiments.contract_assurance.evaluate import evaluate_raw
from experiments.contract_assurance.evaluate import evaluate_initial, evaluate_next_action, evaluate_revision
from experiments.contract_assurance.inventory import discover_response_classes, unregistered_response_classes
from experiments.contract_assurance.audit import BlindBatchAudit
from experiments.contract_assurance.runner import run_deterministic
from experiments.contract_assurance.mutations import write_fixture_manifest
from experiments.contract_assurance.mutations import deduplicate, mutations
from experiments.contract_assurance.lint import lint_contract
from experiments.contract_assurance.registry import contract_registry
from experiments.contract_assurance.report import render_markdown, summarize, summarize_by_contract, write_history
from experiments.contract_assurance.snapshot import fingerprint, write_snapshot
from experiments.contract_assurance.snapshot import validate_public_package
from experiments.contract_assurance.audit import audit_public_package
from experiments.contract_assurance.taxonomy import FailureCode
from investigator.services.contracts import NextActionResponse
from investigator.services.contracts import RevisionResponse
from investigator.services.contracts import InitialResponse
from investigator.services.contracts import NextStepResponse
from investigator.services.contracts import InitialExpansionResponse, HypothesisTransition, UncertaintyTransition


def valid_action():
    return {"selected_action_id": "A1", "target_uncertainty": "An open question.", "expected_information_value": "It may distinguish explanations.", "why_this_action_now": "It is available now."}


def test_real_schema_boundary_classifies_serialization_and_shape():
    assert evaluate_raw("", NextActionResponse).code is FailureCode.S0
    assert evaluate_raw("not json", NextActionResponse).code is FailureCode.S0
    assert evaluate_raw(json.dumps({"selected_action_id": "A1"}), NextActionResponse).code is FailureCode.S1
    assert evaluate_raw(json.dumps(valid_action()), NextActionResponse).accepted


def test_outer_json_fence_is_the_only_normalization():
    assert evaluate_raw("```json\n" + json.dumps(valid_action()) + "\n```", NextActionResponse).accepted
    assert evaluate_raw("before\n" + json.dumps(valid_action()), NextActionResponse).code is FailureCode.S0


def test_canonical_placeholders_are_rejected_by_real_contracts():
    initial = {"hypotheses": [{"id": "H1", "statement": "A", "status": "active", "supported_by": ["E1"], "conflicted_by": [], "unresolved": ["U"], "specificity_basis_evidence_ids": []}], "selected_action_id": "A1", "target_uncertainty": "The uncertainty this enquiry addresses.", "expected_information_value": "Useful value.", "why_this_action_now": "Useful reason."}
    assert evaluate_raw(json.dumps(initial), InitialResponse).code is FailureCode.S1
    placeholder_action = valid_action() | {"selected_action_id": "A9"}
    assert evaluate_raw(json.dumps(placeholder_action), NextActionResponse).code is FailureCode.S2


def test_registry_has_current_service_contracts():
    registry = contract_registry()
    assert {"InitialResponse", "InitialExpansionResponse", "NextActionResponse", "RevisionResponse", "NextStepResponse"} <= set(registry)
    assert "ModelScreenHypothesisResponse" in registry


def test_inventory_discovers_and_registers_all_response_contracts():
    root = Path(__file__).resolve().parents[1]
    discovered = discover_response_classes(root)
    names = {item["name"] for item in discovered}
    assert {"InitialResponse", "InitialExpansionResponse", "NextActionResponse", "RevisionResponse", "HypothesisResponse"} <= names
    assert unregistered_response_classes(root) == []
    assert all(item["source_hash"] for item in __import__("experiments.contract_assurance.inventory", fromlist=["inventory"]).inventory(root)["contracts"])


def test_lint_detects_template_drift():
    spec = contract_registry()["NextActionResponse"]
    issues = lint_contract(spec, template={"selected_action_id": "A1", "stale_field": "x"})
    assert any("forbidden field" in issue.message for issue in issues)


def test_snapshots_are_hashable_and_public(tmp_path: Path):
    spec = contract_registry()["NextActionResponse"]
    path = write_snapshot(spec, tmp_path, prompt="Return JSON", case_input={"case": "public"}, template=valid_action(), commit="abc")
    payload = json.loads(path.read_text())
    assert payload["manifest"]["schema_hash"] == fingerprint(spec.schema.model_json_schema())
    assert "validator" not in json.dumps(payload).lower()
    assert validate_public_package(payload) == []
    contaminated = dict(payload, prior_results=["known failure"])
    assert not audit_public_package(contaminated)["accepted"]


def test_report_summary_counts_failures():
    results = [evaluate_raw("", NextActionResponse), evaluate_raw(json.dumps(valid_action()), NextActionResponse)]
    assert summarize(results) == {"total": 2, "accepted": 1, "rejected": 1, "failure_codes": {"S0": 1}}


def test_report_history_preserves_dated_json_and_markdown(tmp_path: Path):
    latest, dated = write_history(tmp_path, {"accepted": 1, "rejected": 0}, timestamp="2026-08-31T12:00:00+00:00")
    assert latest.name == "latest.json" and dated.parent.name == "history"
    assert "Accepted" in (tmp_path / "latest.md").read_text()
    assert render_markdown({"failure_codes": {"S2": 3}}).count("S2") == 1


def test_report_aggregates_by_contract():
    first = evaluate_raw(json.dumps(valid_action()), NextActionResponse)
    first.details["contract"] = "NextActionResponse"
    second = evaluate_raw("", NextActionResponse)
    second.details["contract"] = "NextActionResponse"
    assert summarize_by_contract([first, second])["NextActionResponse"]["rejected"] == 1


def test_deterministic_runner_is_offline_and_writes_inventory(tmp_path: Path):
    report = run_deterministic(Path(__file__).resolve().parents[1], tmp_path, "abc")
    assert report["blind_results_included"] is False
    assert report["deterministic"]["total"] > 0
    assert report["deterministic"]["total"] >= 35
    assert (tmp_path / "inventory.json").exists()
    assert (tmp_path / "latest.json").exists()


def test_mutations_preserve_provenance_and_deduplicate():
    items = mutations(valid_action(), required_fields=("selected_action_id",))
    assert items[0].raw_output == "" and items[0].intended_code == "S0"
    assert {item.name for item in items} >= {"invented_literal", "id_with_explanation", "placeholder_text"}
    assert len(deduplicate(items)) == len(items)


def test_next_action_mutations_reach_the_expected_failure_stage():
    for mutation in mutations(valid_action(), required_fields=("selected_action_id",)):
        result = evaluate_next_action(mutation.raw_output, {"A1"})
        if mutation.intended_code == "valid":
            assert result.accepted, mutation.name
        else:
            assert not result.accepted, mutation.name


def test_next_step_union_rejects_mixed_branch_fields():
    valid = {"step_type": "action", "selected_action_id": "A1", "target_uncertainty": "An open question.", "expected_information_value": "It may distinguish explanations.", "why_this_action_now": "It is available now.", "conclusion_hypothesis_id": None, "conclusion_reason": None, "remaining_uncertainty_ids": []}
    polluted = dict(valid, conclusion_hypothesis_id="H1")
    result = evaluate_raw(json.dumps(polluted), NextStepResponse)
    assert not result.accepted and result.code is FailureCode.S4


def test_seeded_expansion_rejects_relationship_parent_mismatch():
    payload = {"seed_analysis": {"supported_by": ["E1"], "conflicted_by": [], "unresolved": ["Question"], "specificity_basis_evidence_ids": []}, "competing_hypotheses": [{"id": "H2", "parent_id": "H1", "statement": "Different.", "status": "active", "supported_by": ["E2"], "conflicted_by": [], "unresolved": ["Question"], "specificity_basis_evidence_ids": [], "relationship": "competing_root", "contrasted_hypothesis_id": "H1", "material_difference": "Different cause."}], "selected_action_id": "A1", "target_uncertainty": "Question", "expected_information_value": "Value", "why_this_action_now": "Reason"}
    result = evaluate_raw(json.dumps(payload), InitialExpansionResponse)
    assert not result.accepted and result.code is FailureCode.S4


def test_revision_other_transition_requires_explicit_operation_details():
    with __import__("pytest").raises(ValueError):
        HypothesisTransition(hypothesis_id="H1", transition="other", reason="Need more", requested_operation_name=None, requested_effect=None, why_existing_operations_do_not_fit=None)
    with __import__("pytest").raises(ValueError):
        UncertaintyTransition(uncertainty_id="H1:U1", transition="keep", reason="Keep", requested_operation_name="split")


def test_fixture_manifest_is_reproducible_and_blind_audit_is_strict(tmp_path: Path):
    path = write_fixture_manifest(tmp_path / "fixtures.json", "NextActionResponse", valid_action(), ("selected_action_id",))
    payload = json.loads(path.read_text())
    assert {item["name"] for item in payload["mutations"]} >= {"empty", "remove_selected_action_id", "unexpected_field"}
    assert BlindBatchAudit("w1", "NextActionResponse", "abc", ("package.json",), True, False).qualifies_as_blind
    assert not BlindBatchAudit("w2", "NextActionResponse", "abc", ("repo",), False, True).qualifies_as_blind


def test_availability_is_checked_after_schema():
    result = evaluate_next_action(json.dumps(valid_action()), {"A2"})
    assert not result.accepted and result.code is FailureCode.S3


def test_revision_operation_preflight_rejects_unknown_reference():
    from investigator.environments.case_01 import Case1ControlledEnvironment
    from investigator.services.contracts import InitialResponse
    environment = Case1ControlledEnvironment(Path(__file__).resolve().parents[1] / "experiments/investigation_smoke/case_01/artifacts")
    state = environment.build_initial_state(InitialResponse.model_validate({"hypotheses": [{"id": "H1", "statement": "A", "status": "active", "supported_by": ["E1"], "conflicted_by": [], "unresolved": ["U"], "specificity_basis_evidence_ids": []}], **valid_action()}))
    raw = {"hypothesis_updates": [{"hypothesis_id": "H1", "transition": "keep", "reason": "r", "add_supporting_evidence_ids": ["E999"]}], "new_hypotheses": [], "uncertainty_updates": [], "new_uncertainties": [], "revision_rationale": "r"}
    result = evaluate_revision(json.dumps(raw), state)
    assert not result.accepted and result.code is FailureCode.S3


def test_initial_state_boundary_rejects_unknown_evidence_without_mutation():
    from investigator.services.contracts import InitialResponse
    bad = {"hypotheses": [{"id": "H1", "statement": "A", "status": "active", "supported_by": ["E999"], "conflicted_by": [], "unresolved": ["U"], "specificity_basis_evidence_ids": []}], **valid_action()}
    result = evaluate_initial(json.dumps(bad), schema=InitialResponse, build_state=lambda response: (_ for _ in ()).throw(ValueError("Unknown evidence ID: E999")))
    assert not result.accepted and result.code is FailureCode.S3 and result.stage == "state_operation_preflight"
