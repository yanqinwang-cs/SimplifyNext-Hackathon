import json
import subprocess
from pathlib import Path

from experiments.contract_assurance.evaluate import evaluate_raw
from experiments.contract_assurance.evaluate import evaluate_initial, evaluate_next_action, evaluate_revision, persist_evaluations
from experiments.contract_assurance.inventory import assert_complete_inventory, assert_inventory_paths, discover_response_classes, render_inventory_markdown, unregistered_response_classes, write_inventory_markdown
from experiments.contract_assurance.audit import BlindBatchAudit, audit_batch_package, record_batch
from experiments.contract_assurance.evolution.records import validate_evolution_record
from experiments.contract_assurance.runner import run_deterministic, verify_committed_packages
from experiments.contract_assurance.mutations import write_fixture_manifest
from experiments.contract_assurance.mutations import deduplicate, mutations
from experiments.contract_assurance.lint import lint_contract
from experiments.contract_assurance.registry import contract_registry, validate_registry
from experiments.contract_assurance.report import coverage_ledger, failure_rate_statistics, render_markdown, summarize, summarize_blind, summarize_blind_by_role, summarize_by_contract, write_history
from experiments.contract_assurance.snapshot import fingerprint, verify_snapshot, verify_snapshot_against_contract, write_public_snapshot, write_snapshot
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
    assert evaluate_raw({**valid_action(), "selected_action_id": "A9"}, NextActionResponse).code is FailureCode.S2
    assert evaluate_raw("```json\n" + json.dumps(valid_action()), NextActionResponse).code is FailureCode.S0
    assert evaluate_raw("[" + json.dumps(valid_action()) + "]", NextActionResponse).code is FailureCode.S1
    assert evaluate_raw(json.dumps(valid_action()) + "\n" + json.dumps(valid_action()), NextActionResponse).code is FailureCode.S0
    assert evaluate_raw(b'{"selected_action_id":"A1"}', NextActionResponse).code is FailureCode.S0


def test_canonical_placeholders_are_rejected_by_real_contracts():
    initial = {"hypotheses": [{"id": "H1", "statement": "A", "status": "active", "supported_by": ["E1"], "conflicted_by": [], "unresolved": ["U"], "specificity_basis_evidence_ids": []}], "selected_action_id": "A1", "target_uncertainty": "The uncertainty this enquiry addresses.", "expected_information_value": "Useful value.", "why_this_action_now": "Useful reason."}
    assert evaluate_raw(json.dumps(initial), InitialResponse).code is FailureCode.S1
    placeholder_action = valid_action() | {"selected_action_id": "A9"}
    assert evaluate_raw(json.dumps(placeholder_action), NextActionResponse).code is FailureCode.S2
    revision = {"hypothesis_updates": [], "new_hypotheses": [], "uncertainty_updates": [], "new_uncertainties": [], "revision_rationale": "How the evidence changed the state."}
    assert not evaluate_raw(json.dumps(revision), RevisionResponse).accepted
    whitespace_placeholder = valid_action() | {"target_uncertainty": "  REPLACE_WITH_TARGET_UNCERTAINTY  "}
    assert not evaluate_raw(json.dumps(whitespace_placeholder), NextActionResponse).accepted


def test_registry_has_current_service_contracts():
    registry = contract_registry()
    assert {"InitialResponse", "InitialExpansionResponse", "NextActionResponse", "RevisionResponse", "NextStepResponse"} <= set(registry)
    assert "ModelScreenHypothesisResponse" in registry
    assert all(not lint_contract(spec) for spec in registry.values())
    validate_registry()


def test_inventory_discovers_and_registers_all_response_contracts(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    discovered = discover_response_classes(root)
    names = {item["name"] for item in discovered}
    assert {"InitialResponse", "InitialExpansionResponse", "NextActionResponse", "RevisionResponse", "HypothesisResponse"} <= names
    assert unregistered_response_classes(root) == []
    assert_complete_inventory(root)
    assert_inventory_paths(root)
    assert all(item["source_hash"] for item in __import__("experiments.contract_assurance.inventory", fromlist=["inventory"]).inventory(root)["contracts"])
    assert all(item["prompt_hashes"] or item["name"] == "NextStepResponse" for item in __import__("experiments.contract_assurance.inventory", fromlist=["inventory"]).inventory(root)["contracts"])
    markdown = render_inventory_markdown(__import__("experiments.contract_assurance.inventory", fromlist=["inventory"]).inventory(root))
    assert "LLM-facing contract inventory" in markdown and "InitialResponse" in markdown
    assert write_inventory_markdown(root, tmp_path / "inventory").exists()


def test_lint_detects_template_drift():
    spec = contract_registry()["NextActionResponse"]
    issues = lint_contract(spec, template={"selected_action_id": "A1", "stale_field": "x"})
    assert any("forbidden field" in issue.message for issue in issues)
    assert not any("extra='forbid'" in issue.message for issue in lint_contract(spec))
    assert any("canonical placeholder text" in issue.message for issue in lint_contract(spec, template={"selected_action_id": "A1", "target_uncertainty": "The uncertainty this enquiry addresses."}))


def test_snapshots_are_hashable_and_public(tmp_path: Path):
    spec = contract_registry()["NextActionResponse"]
    path = write_snapshot(spec, tmp_path, prompt="Return JSON", case_input={"case": "public"}, template=valid_action(), commit="abc")
    payload = json.loads(path.read_text())
    assert payload["manifest"]["schema_hash"] == fingerprint(spec.schema.model_json_schema())
    assert "validator" not in json.dumps(payload).lower()
    assert validate_public_package(payload) == []
    assert verify_snapshot(payload) == []
    assert verify_snapshot_against_contract(payload, spec) == []
    payload["prompt"] = "tampered"
    assert "prompt_hash drift" in verify_snapshot(payload)
    contaminated = dict(payload, prior_results=["known failure"])
    assert not audit_public_package(contaminated)["accepted"]
    safe_path = write_public_snapshot(spec, tmp_path / "safe", prompt="Return JSON", case_input={"case": "public"}, template=valid_action(), commit="abc")
    assert safe_path.exists()
    with __import__("pytest").raises(ValueError, match="Public snapshot rejected"):
        write_public_snapshot(spec, tmp_path / "unsafe", prompt="Return JSON; validator implementation is hidden", case_input={"case": "public"}, template=valid_action(), commit="abc")


def test_blind_batch_audit_verifies_exact_package_and_isolation(tmp_path: Path):
    spec = contract_registry()["NextActionResponse"]
    path = write_public_snapshot(spec, tmp_path, prompt="Return JSON", case_input={"case": "public"}, template=valid_action(), commit="abc")
    package = json.loads(path.read_text())
    audit = BlindBatchAudit("worker", spec.name, __import__("experiments.contract_assurance.snapshot", fromlist=["fingerprint"]).fingerprint(package), (path.name,), True, False)
    assert audit_batch_package(path, audit) == {"accepted": True, "issues": []}
    assert "batch package hash mismatch" in audit_batch_package(path, BlindBatchAudit("worker", spec.name, "stale", (path.name,), True, False))["issues"]


def test_record_batch_persists_exact_outputs_and_status(tmp_path: Path):
    audit = BlindBatchAudit("worker", "NextActionResponse", "package-hash", ("package.json",), False, True, ("shared filesystem",))
    result = evaluate_raw("not-json", NextActionResponse)
    destination = record_batch(tmp_path, audit, [result])
    assert json.loads((destination / "evaluations.json").read_text())[0]["raw_output"] == "not-json"
    manifest = json.loads((destination / "batch_manifest.json").read_text())
    assert manifest["blind_status"] == "NOT_BLIND" and manifest["evaluation_count"] == 1


def test_report_summary_counts_failures():
    results = [evaluate_raw("", NextActionResponse), evaluate_raw(json.dumps(valid_action()), NextActionResponse)]
    assert summarize(results) == {"total": 2, "accepted": 1, "rejected": 1, "failure_codes": {"S0": 1}, "s5_candidates": 0, "s6_limitations": 0}


def test_report_history_preserves_dated_json_and_markdown(tmp_path: Path):
    latest, dated = write_history(tmp_path, {"accepted": 1, "rejected": 0}, timestamp="2026-08-31T12:00:00+00:00")
    assert latest.name == "latest.json" and dated.parent.name == "history"
    assert "Accepted" in (tmp_path / "latest.md").read_text()
    assert "By contract" in render_markdown({"deterministic_by_contract": {"NextActionResponse": {"total": 1, "accepted": 1, "rejected": 0}}})
    assert render_markdown({"failure_codes": {"S2": 3}}).count("S2") == 1


def test_report_aggregates_by_contract():
    first = evaluate_raw(json.dumps(valid_action()), NextActionResponse)
    first.details["contract"] = "NextActionResponse"
    second = evaluate_raw("", NextActionResponse)
    second.details["contract"] = "NextActionResponse"
    assert summarize_by_contract([first, second])["NextActionResponse"]["rejected"] == 1


def test_persisted_results_retain_raw_output_and_classification(tmp_path: Path):
    result = evaluate_raw("not-json", NextActionResponse)
    path = persist_evaluations([result], tmp_path / "results.json")
    payload = json.loads(path.read_text())
    assert payload[0]["raw_output"] == "not-json" and payload[0]["code"] == "S0"


def test_not_blind_batches_are_excluded_from_statistics():
    qualified = evaluate_raw(json.dumps(valid_action()), NextActionResponse)
    qualified.details["worker_id"] = "blind"
    contaminated = evaluate_raw("", NextActionResponse)
    contaminated.details["worker_id"] = "contaminated"
    audits = [BlindBatchAudit("blind", "NextActionResponse", "a", ("package",), True, False), BlindBatchAudit("contaminated", "NextActionResponse", "b", ("repo",), False, True)]
    result = summarize_blind([qualified, contaminated], audits)
    assert result["total"] == 1 and result["accepted"] == 1 and result["excluded_not_blind"] == 1


def test_blind_role_summaries_are_separate_and_filtered():
    producer = evaluate_raw(json.dumps(valid_action()), NextActionResponse)
    producer.details.update({"worker_id": "p", "role": "producer"})
    adversary = evaluate_raw("", NextActionResponse)
    adversary.details.update({"worker_id": "a", "role": "adversary"})
    audits = [BlindBatchAudit("p", "NextActionResponse", "p", ("package",), True, False), BlindBatchAudit("a", "NextActionResponse", "a", ("package",), True, False)]
    by_role = summarize_blind_by_role([producer, adversary], audits)
    assert by_role["producer"]["accepted"] == 1 and by_role["adversary"]["failure_codes"] == {"S0": 1}


def test_failure_rate_statistics_reports_observed_and_upper_bound():
    result = failure_rate_statistics([evaluate_raw(json.dumps(valid_action()), NextActionResponse), evaluate_raw("", NextActionResponse)])
    assert result["failures"] == 1 and result["total"] == 2
    assert result["observed_failure_rate"] == 0.5
    assert result["upper_failure_rate"] >= result["observed_failure_rate"]


def test_coverage_ledger_deduplicates_failure_signatures_with_counts():
    first = evaluate_raw("", NextActionResponse)
    first.details["contract"] = "NextActionResponse"
    second = evaluate_raw("", NextActionResponse)
    second.details["contract"] = "NextActionResponse"
    ledger = coverage_ledger([first, second])
    assert len(ledger) == 1 and next(iter(ledger.values())) == 2


def test_deterministic_runner_is_offline_and_writes_inventory(tmp_path: Path):
    report = run_deterministic(Path(__file__).resolve().parents[1], tmp_path, "abc")
    assert report["blind_results_included"] is False
    assert report["deterministic"]["total"] > 0
    assert report["deterministic"]["total"] >= 35
    assert "unexpected_accepts" in report["deterministic"] and "unexpected_rejects" in report["deterministic"]
    assert report["coverage_ledger"]
    assert (tmp_path / "inventory.json").exists()
    assert (tmp_path / "latest.json").exists()


def test_assurance_module_cli_reports_summary(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(["uv", "run", "python", "-m", "experiments.contract_assurance", "--root", str(root), "--output", str(tmp_path), "--commit", "abc"], cwd=root, capture_output=True, text=True, check=True, env={**__import__("os").environ, "UV_CACHE_DIR": "/tmp/codex_uv_contract_assurance"})
    assert "contracts=7" in completed.stdout and "unexpected_accepts=0" in completed.stdout


def test_mutations_preserve_provenance_and_deduplicate():
    items = mutations(valid_action(), required_fields=("selected_action_id",))
    assert items[0].raw_output == "" and items[0].intended_code == "S0"
    assert {item.name for item in items} >= {"invented_literal", "id_with_explanation", "placeholder_text", "wrong_primitive_selected_action_id"}
    assert len(deduplicate(items)) == len(items)


def test_contract_mutations_cover_nested_shape_and_cross_field_rules():
    expansion = {
        "seed_analysis": {"supported_by": ["E1"], "conflicted_by": [], "unresolved": ["Question"], "specificity_basis_evidence_ids": []},
        "competing_hypotheses": [{"id": "H2", "parent_id": None, "statement": "Different.", "status": "active", "supported_by": ["E2"], "conflicted_by": [], "unresolved": ["Question"], "specificity_basis_evidence_ids": [], "relationship": "competing_root", "contrasted_hypothesis_id": "H1", "material_difference": "Different cause."}],
        "selected_action_id": "A1", "target_uncertainty": "Question", "expected_information_value": "Value", "why_this_action_now": "Reason",
    }
    names = {item.name for item in mutations(expansion, required_fields=("competing_hypotheses",), contract="InitialExpansionResponse")}
    assert {"unexpected_nested_competing_hypotheses", "empty_required_competing_hypotheses", "competing_root_with_parent"} <= names


def test_id_array_mutations_generate_wrong_namespace_challenges():
    value = {"evidence_ids": ["E1"], "selected_action_id": "A1"}
    assert any(item.name == "wrong_namespace_evidence_ids" and item.intended_code == "S2" for item in mutations(value))


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
    polluted_stop = {"step_type": "stop_unresolved", "selected_action_id": "A1", "target_uncertainty": "x", "expected_information_value": None, "why_this_action_now": None, "conclusion_hypothesis_id": None, "conclusion_reason": "Cannot continue.", "remaining_uncertainty_ids": []}
    stop_result = evaluate_raw(json.dumps(polluted_stop), NextStepResponse)
    assert not stop_result.accepted and stop_result.code is FailureCode.S4


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


def test_blind_role_instructions_exist_without_validator_details():
    root = Path(__file__).resolve().parents[1] / "experiments/contract_assurance/blind"
    producer = (root / "producer.md").read_text()
    adversary = (root / "adversary.md").read_text()
    assert "supplied frozen public package" in producer
    assert "subtle outputs" in adversary
    assert "failure taxonomy" not in producer


def test_all_committed_public_packages_match_registered_contracts():
    packages = Path(__file__).resolve().parents[1] / "experiments/contract_assurance/blind/packages"
    registry = contract_registry()
    for path in packages.glob("*.json"):
        payload = json.loads(path.read_text())
        contract = payload["manifest"]["contract"]
        assert contract in registry
        assert verify_snapshot_against_contract(payload, registry[contract]) == [], path.name
    assert verify_committed_packages(Path(__file__).resolve().parents[1]) == []


def test_evolution_records_require_semantic_and_regression_evidence():
    assert "legitimate_semantic_need" in validate_evolution_record({"change_id": "C1"})
    record = {field: "present" for field in ("change_id", "contract", "discovery_source", "baseline_failure", "classification", "legitimate_semantic_need", "why_existing_contract_insufficient", "implementation_change", "tests_added", "expected_reclassifications", "unexpected_regressions", "before_after_compliance", "commit")}
    assert validate_evolution_record(record) == []


def test_registered_experiment_contracts_forbid_unexpected_fields():
    from experiments.model_screen.schemas import HypothesisResponse
    valid = {"hypotheses": [{"statement": "A", "justification": "Because", "uncertainty": "Unknown"}, {"statement": "B", "justification": "Because", "uncertainty": "Unknown"}]}
    with __import__("pytest").raises(ValueError):
        HypothesisResponse.model_validate({**valid, "unexpected": True})
    with __import__("pytest").raises(ValueError):
        HypothesisResponse.model_validate({"hypotheses": [{"statement": "A", "justification": "Because", "uncertainty": "Unknown", "unexpected": True}, valid["hypotheses"][1]]})


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


def test_real_case_state_rejects_unreleased_valid_looking_evidence():
    from investigator.environments.case_01 import Case1ControlledEnvironment
    from investigator.services.contracts import InitialResponse
    environment = Case1ControlledEnvironment(Path(__file__).resolve().parents[1] / "experiments/investigation_smoke/case_01/artifacts")
    state = environment.build_initial_state(InitialResponse.model_validate({"hypotheses": [{"id": "H1", "statement": "A", "status": "active", "supported_by": ["E1"], "conflicted_by": [], "unresolved": ["U"], "specificity_basis_evidence_ids": []}], **valid_action()}))
    raw = {"hypothesis_updates": [{"hypothesis_id": "H1", "transition": "keep", "reason": "r", "add_supporting_evidence_ids": ["A2_RELEASE"]}], "new_hypotheses": [], "uncertainty_updates": [], "new_uncertainties": [], "revision_rationale": "r"}
    result = evaluate_revision(json.dumps(raw), state)
    assert not result.accepted and result.code is FailureCode.S3
    assert "A2_RELEASE" not in state.evidence
