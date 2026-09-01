import json
import subprocess
from dataclasses import replace
from pathlib import Path

from experiments.contract_assurance.evaluate import evaluate_raw
from experiments.contract_assurance.evaluate import evaluate_initial, evaluate_next_action, evaluate_revision, persist_evaluations
from experiments.contract_assurance.inventory import assert_complete_inventory, assert_inventory_paths, discover_response_classes, render_inventory_markdown, unregistered_response_classes, write_inventory_markdown
from experiments.contract_assurance.audit import BlindBatchAudit, audit_batch_package, record_batch
from experiments.contract_assurance.evolution.records import validate_evolution_record
from experiments.contract_assurance.runner import blind_compliance_summary, run_deterministic, verify_committed_packages
from experiments.contract_assurance.mutations import write_fixture_manifest
from experiments.contract_assurance.mutations import deduplicate, mutations
from experiments.contract_assurance.lint import lint_contract
from experiments.contract_assurance.registry import contract_registry, validate_registry
from experiments.contract_assurance.report import coverage_ledger, deterministic_correctness, failure_rate_statistics, render_markdown, summarize, summarize_blind, summarize_blind_by_role, summarize_by_contract, write_history
from experiments.contract_assurance.snapshot import fingerprint, verify_snapshot, verify_snapshot_against_contract, write_public_snapshot, write_snapshot
from experiments.contract_assurance.snapshot import validate_public_package
from experiments.contract_assurance.audit import audit_public_package
from experiments.contract_assurance.taxonomy import FailureCode
from investigator.services.contracts import NextActionResponse
from investigator.services.contracts import RevisionResponse
from investigator.services.contracts import InitialResponse
from investigator.services.contracts import NextStepResponse
from investigator.services.contracts import InitialExpansionResponse, HypothesisTransition, UncertaintyTransition
from investigator.services.contracts import NewUncertainty
from experiments.model_screen.schemas import HypothesisResponse
from scripts.smoke_bedrock import SmokeResponse


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
    assert evaluate_raw("[" + json.dumps(valid_action()) + "]", NextActionResponse).code is FailureCode.S0
    assert evaluate_raw(json.dumps(valid_action()) + "\n" + json.dumps(valid_action()), NextActionResponse).code is FailureCode.S0
    assert evaluate_raw(b'{"selected_action_id":"A1"}', NextActionResponse).code is FailureCode.S0


def test_canonical_placeholders_are_rejected_by_real_contracts():
    initial = {"hypotheses": [{"id": "H1", "statement": "A", "status": "active", "supported_by": ["E1"], "conflicted_by": [], "unresolved": ["U"], "specificity_basis_evidence_ids": []}], "selected_action_id": "A1", "target_uncertainty": "The uncertainty this enquiry addresses.", "expected_information_value": "Useful value.", "why_this_action_now": "Useful reason."}
    assert evaluate_raw(json.dumps(initial), InitialResponse).code is FailureCode.S4
    placeholder_action = valid_action() | {"selected_action_id": "A9"}
    assert evaluate_raw(json.dumps(placeholder_action), NextActionResponse).code is FailureCode.S2
    revision = {"hypothesis_updates": [], "new_hypotheses": [], "uncertainty_updates": [], "new_uncertainties": [], "revision_rationale": "How the evidence changed the state."}
    assert not evaluate_raw(json.dumps(revision), RevisionResponse).accepted
    whitespace_placeholder = valid_action() | {"target_uncertainty": "  REPLACE_WITH_TARGET_UNCERTAINTY  "}
    assert not evaluate_raw(json.dumps(whitespace_placeholder), NextActionResponse).accepted
    initial_unresolved = initial | {"hypotheses": [initial["hypotheses"][0] | {"unresolved": ["The uncertainty this enquiry addresses."]}]}
    assert evaluate_raw(json.dumps(initial_unresolved), InitialResponse).code is FailureCode.S4


def test_null_initial_hypotheses_reaches_schema_validation():
    payload = {"hypotheses": None, **valid_action()}
    result = evaluate_raw(json.dumps(payload), InitialResponse)
    assert not result.accepted and result.code is FailureCode.S1


def test_registry_has_current_service_contracts():
    registry = contract_registry()
    assert {"InitialResponse", "InitialExpansionResponse", "NextActionResponse", "RevisionResponse", "NextStepResponse"} <= set(registry)
    assert "ModelScreenHypothesisResponse" in registry
    assert all(not lint_contract(spec) for spec in registry.values())
    validate_registry()
    documentation = (Path(__file__).resolve().parents[1] / "docs/schema-contracts.md").read_text(encoding="utf-8")
    assert all(f"| {code} |" in documentation for code in ("S0", "S1", "S2", "S3", "S4", "S5", "S6"))


def test_inventory_discovers_and_registers_all_response_contracts(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    discovered = discover_response_classes(root)
    names = {item["name"] for item in discovered}
    assert {"InitialResponse", "InitialExpansionResponse", "NextActionResponse", "RevisionResponse", "HypothesisResponse"} <= names
    assert unregistered_response_classes(root) == []
    assert __import__("experiments.contract_assurance.inventory", fromlist=["inventory"]).inventory(root)["dynamic_structured_boundaries"] == [{"path": "experiments/gate1/runner.py", "symbol": "ExperimentRunner.run", "reason": "output_schema is supplied by the caller; the concrete schema must be registered at its call site"}]
    assert_complete_inventory(root)
    assert_inventory_paths(root)
    assert all(item["source_hash"] for item in __import__("experiments.contract_assurance.inventory", fromlist=["inventory"]).inventory(root)["contracts"])
    assert all(item["prompt_hashes"] or item["name"] == "NextStepResponse" for item in __import__("experiments.contract_assurance.inventory", fromlist=["inventory"]).inventory(root)["contracts"])
    inventory_data = __import__("experiments.contract_assurance.inventory", fromlist=["inventory"]).inventory(root)
    assert all(item["template_source"] and item["template_hash"] for item in inventory_data["contracts"])
    assert all(item["parser_entry_point"] and item["normalization_behavior"] and item["schema_validation"] and item["field_namespace_validation"] and item["referential_validation"] and item["availability_validation"] and item["cross_field_validation"] and item["deterministic_consumer"] and item["raw_output_preserved_on_failure"] for item in inventory_data["contracts"])
    markdown = render_inventory_markdown(__import__("experiments.contract_assurance.inventory", fromlist=["inventory"]).inventory(root))
    assert "LLM-facing contract inventory" in markdown and "InitialResponse" in markdown
    assert write_inventory_markdown(root, tmp_path / "inventory").exists()


def test_lint_detects_template_drift():
    spec = contract_registry()["NextActionResponse"]
    issues = lint_contract(spec, template={"selected_action_id": "A1", "stale_field": "x"})
    assert any("forbidden field" in issue.message for issue in issues)
    assert not any("extra='forbid'" in issue.message for issue in lint_contract(spec))
    assert any("canonical placeholder text" in issue.message for issue in lint_contract(spec, template={"selected_action_id": "A1", "target_uncertainty": "The uncertainty this enquiry addresses."}))
    assert any("unregistered placeholder sentinel" in issue.message for issue in lint_contract(replace(spec, template_placeholders=()), template={"selected_action_id": "REPLACE_WITH_ACTION"}))
    assert any("prompt contains canonical placeholder text" in issue.message for issue in lint_contract(spec, prompt="How the evidence changed the state."))


def test_revision_prompt_uses_substantive_template_exemplar():
    from investigator.environments.case_01 import Case1ControlledEnvironment
    from investigator.environments.case_01_prompts import initial_expansion_prompt, initial_prompt, next_action_prompt, revision_prompt
    from types import SimpleNamespace
    environment = Case1ControlledEnvironment(Path(__file__).resolve().parents[1] / "experiments/investigation_smoke/case_01/artifacts")
    session = SimpleNamespace(case_state=environment.build_initial_state(InitialResponse.model_validate({"hypotheses": [{"id": "H1", "statement": "A", "status": "active", "supported_by": ["E1"], "conflicted_by": [], "unresolved": ["U"], "specificity_basis_evidence_ids": []}], **valid_action()})))
    release = SimpleNamespace(action_id="A1", artifact_id="A1_RELEASE", content="Released evidence.")
    assert "How the evidence changed the state." not in revision_prompt(environment, session, release)
    prompts = [
        initial_prompt(environment),
        initial_expansion_prompt(environment, "A human-seeded explanation."),
        next_action_prompt(environment, session, environment.available_actions(set())),
        revision_prompt(environment, session, release),
    ]
    canonical = {"The uncertainty this enquiry addresses.", "How the result could change the explanation space.", "Why this enquiry is useful now.", "How the evidence changed the state."}
    assert all(not any(value in prompt for value in canonical) for prompt in prompts)


def test_lint_detects_nested_required_and_minimum_collection_drift():
    spec = contract_registry()["InitialResponse"]
    issues = lint_contract(spec, template={"hypotheses": [], "selected_action_id": "A1", "target_uncertainty": "q", "expected_information_value": "v", "why_this_action_now": "r"})
    assert any("empty list for non-empty field 'hypotheses'" in issue.message for issue in issues)
    nested = lint_contract(spec, template={"hypotheses": [{}], "selected_action_id": "A1", "target_uncertainty": "q", "expected_information_value": "v", "why_this_action_now": "r"})
    assert any("hypotheses[0].id" in issue.message for issue in nested)


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
    audit = BlindBatchAudit("worker", spec.name, __import__("experiments.contract_assurance.snapshot", fromlist=["fingerprint"]).fingerprint(package), (path.name,), True, False, isolation_evidence=("repository read denied probe",))
    assert audit_batch_package(path, audit) == {"accepted": True, "issues": []}
    assert "batch package hash mismatch" in audit_batch_package(path, BlindBatchAudit("worker", spec.name, "stale", (path.name,), True, False, isolation_evidence=("repository read denied probe",)))["issues"]
    disclosed = BlindBatchAudit("worker", spec.name, __import__("experiments.contract_assurance.snapshot", fromlist=["fingerprint"]).fingerprint(package), ("package.json", "src/investigator/services/contracts.py"), True, False)
    assert not disclosed.qualifies_as_blind
    assert "implementation or repository paths" in audit_batch_package(path, disclosed)["issues"][-2]


def test_record_batch_persists_exact_outputs_and_status(tmp_path: Path):
    audit = BlindBatchAudit("worker", "NextActionResponse", "package-hash", ("package.json",), False, True, contamination_risks=("shared filesystem",))
    result = evaluate_raw("not-json", NextActionResponse)
    destination = record_batch(tmp_path, audit, [result])
    assert json.loads((destination / "evaluations.json").read_text())[0]["raw_output"] == "not-json"
    manifest = json.loads((destination / "batch_manifest.json").read_text())
    assert manifest["blind_status"] == "NOT_BLIND" and manifest["evaluation_count"] == 1


def test_report_summary_counts_failures():
    results = [evaluate_raw("", NextActionResponse), evaluate_raw(json.dumps(valid_action()), NextActionResponse)]
    assert summarize(results) == {"total": 2, "accepted": 1, "rejected": 1, "failure_codes": {"S0": 1}, "stage_counts": {"schema": 1, "serialization": 1}, "s5_candidates": 0, "s6_limitations": 0}


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
    audits = [BlindBatchAudit("blind", "NextActionResponse", "a", ("package",), True, False, isolation_evidence=("probe",)), BlindBatchAudit("contaminated", "NextActionResponse", "b", ("repo",), False, True)]
    result = summarize_blind([qualified, contaminated], audits)
    assert result["total"] == 1 and result["accepted"] == 1 and result["excluded_not_blind"] == 1


def test_blind_role_summaries_are_separate_and_filtered():
    producer = evaluate_raw(json.dumps(valid_action()), NextActionResponse)
    producer.details.update({"worker_id": "p", "role": "producer"})
    adversary = evaluate_raw("", NextActionResponse)
    adversary.details.update({"worker_id": "a", "role": "adversary"})
    audits = [BlindBatchAudit("p", "NextActionResponse", "p", ("package",), True, False, isolation_evidence=("probe",)), BlindBatchAudit("a", "NextActionResponse", "a", ("package",), True, False, isolation_evidence=("probe",))]
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
    assert report["prompt_lint"] == {"status": "clean", "issues": []}
    assert "## Prompt/schema/template lint" in render_markdown(report)
    assert "## Contract provenance" in render_markdown(report)
    assert "| Contract | Production path | Schema hash | Prompt hash(es) | Template hash |" in render_markdown(report)
    assert "| Contract | Total | Accepted | Rejected | Valid pass | Invalid reject | S0 | S1 | S2 | S3 | S4 |" in render_markdown(report)
    assert report["deterministic_failure_rate"]["confidence"] == 0.95
    assert 0 <= report["deterministic_failure_rate"]["upper_failure_rate"] <= 1
    assert report["deterministic_correctness"]["valid_pass_rate"] == 1.0
    assert report["deterministic_correctness"]["invalid_rejection_rate"] == 1.0
    assert "| Contract | Total | Accepted | Rejected | Valid pass | Invalid reject | S0 | S1 | S2 | S3 | S4 | S5 | S6 | Unexpected accepts | Unexpected rejects |" in render_markdown(report)
    assert report["regressions"]["status"] == "clean"
    assert report["changes_made"] and report["remaining_risks"]
    assert report["blind_compliance"]["qualified_failure_codes"]
    assert report["blind_compliance"]["qualified_output_metrics"]["outputs"] == report["blind_compliance"]["qualified_evaluations"]
    assert report["blind_compliance"]["coverage_gaps"] == []
    assert report["deterministic"]["total"] > 0
    assert report["deterministic"]["total"] >= 35
    assert report["deterministic_by_contract"]["StewardDecisionResponse"]["stage_counts"]["coordinator"] == 13
    assert "unexpected_accepts" in report["deterministic"] and "unexpected_rejects" in report["deterministic"]
    assert report["coverage_ledger"]
    assert report["blind_compliance"]["status"] == "NOT_BLIND"
    assert "producer" in report["blind_compliance"]["by_role"]
    assert report["semantic_limitations"] and "S6" in report["semantic_limitations"][0]
    assert report["human_review_required"] is False
    assert report["blind_compliance"]["by_contract"]
    assert report["blind_compliance"]["by_contract_role"]
    assert "| Blind contract/role | Batches | Qualified | Excluded | Evaluations | Accepted | Rejected |" in render_markdown(report)
    assert (tmp_path / "inventory.json").exists()
    assert (tmp_path / "latest.json").exists()


def test_blind_compliance_scans_nested_batches_and_records_outcomes(tmp_path: Path):
    batch = tmp_path / "experiments/contract_assurance/results/role/producer"
    batch.mkdir(parents=True)
    (batch / "batch_manifest.json").write_text(json.dumps({
        "status": "NOT_BLIND",
        "audits": [{"contract": "NextActionResponse", "qualifies_as_blind": False, "worker_id": "producer-1"}],
    }), encoding="utf-8")
    (batch / "evaluations.json").write_text(json.dumps([
        {"accepted": True, "code": None}, {"accepted": False, "code": "S0"},
    ]), encoding="utf-8")
    summary = blind_compliance_summary(tmp_path)
    assert summary["batches"] == 1 and summary["excluded_not_blind"] == 1
    assert summary["by_role"]["producer"]["recorded_evaluations"] == 2
    assert summary["by_role"]["producer"]["recorded_accepted"] == 1
    assert summary["by_role"]["producer"]["recorded_rejected"] == 1
    assert summary["qualified_failure_codes"] == {}

    direct = tmp_path / "experiments/contract_assurance/results/direct/adversary"
    direct.mkdir(parents=True)
    (direct / "batch_manifest.json").write_text(json.dumps({
        "blind_status": "BLIND", "worker_id": "isolated-adversary-1", "contract": "NextActionResponse",
        "qualifies_as_blind": True,
    }), encoding="utf-8")
    (direct / "evaluations.json").write_text(json.dumps([{"accepted": False, "code": "S1"}]), encoding="utf-8")
    summary = blind_compliance_summary(tmp_path)
    assert summary["qualified_failure_codes"] == {"S1": 1}

    mismatch = tmp_path / "experiments/contract_assurance/results/direct/mismatch"
    mismatch.mkdir(parents=True)
    (mismatch / "batch_manifest.json").write_text(json.dumps({"blind_status": "BLIND", "worker_id": "isolated-producer-2", "contract": "NextActionResponse", "qualifies_as_blind": True, "evaluation_count": 2}), encoding="utf-8")
    (mismatch / "evaluations.json").write_text(json.dumps([{"accepted": True}]), encoding="utf-8")
    summary = blind_compliance_summary(tmp_path)
    assert summary["qualified_batches"] == 1

    package_dir = tmp_path / "experiments/contract_assurance/blind/packages"
    package_dir.mkdir(parents=True)
    source_package = Path(__file__).resolve().parents[1] / "experiments/contract_assurance/blind/packages/NextActionResponse.json"
    (package_dir / "NextActionResponse.json").write_text(source_package.read_text(encoding="utf-8"), encoding="utf-8")
    hash_mismatch = tmp_path / "experiments/contract_assurance/results/direct/hash-mismatch"
    hash_mismatch.mkdir(parents=True)
    (hash_mismatch / "batch_manifest.json").write_text(json.dumps({"blind_status": "BLIND", "worker_id": "isolated-producer-3", "contract": "NextActionResponse", "qualifies_as_blind": True, "package_hash": "stale"}), encoding="utf-8")
    (hash_mismatch / "evaluations.json").write_text(json.dumps([{"accepted": True}]), encoding="utf-8")
    summary = blind_compliance_summary(tmp_path)
    assert summary["qualified_batches"] == 1
    (direct / "evaluations.json").write_text(json.dumps([{"accepted": False}]), encoding="utf-8")
    summary = blind_compliance_summary(tmp_path)
    assert summary["qualified_batches"] == 1
    assert summary["by_role"]["adversary"]["evaluations"] == 1


def test_new_uncertainty_description_must_be_substantive():
    result = evaluate_raw(json.dumps({"id": "H1:U2", "hypothesis_id": "H1", "description": ""}), NewUncertainty)
    assert not result.accepted and result.code is FailureCode.S4


def test_revision_transition_reasons_must_be_substantive():
    hypothesis = evaluate_raw(json.dumps({"hypothesis_id": "H1", "transition": "keep", "reason": ""}), HypothesisTransition)
    uncertainty = evaluate_raw(json.dumps({"uncertainty_id": "H1:U1", "transition": "keep", "reason": ""}), UncertaintyTransition)
    assert hypothesis.code is FailureCode.S4 and uncertainty.code is FailureCode.S4
    placeholder = evaluate_raw(json.dumps({"hypothesis_id": "H1", "transition": "keep", "reason": "REPLACE_WITH_REASON"}), HypothesisTransition)
    assert placeholder.code is FailureCode.S4


def test_revision_allows_only_one_transition_per_entity():
    value = {"hypothesis_updates": [{"hypothesis_id": "H1", "transition": "keep", "reason": "Keep it."}, {"hypothesis_id": "H1", "transition": "weaken", "reason": "Weaken it."}], "uncertainty_updates": [], "new_hypotheses": [], "new_uncertainties": [], "revision_rationale": "The evidence updates the state."}
    result = evaluate_raw(json.dumps(value), RevisionResponse)
    assert result.code is FailureCode.S4


def test_revision_transition_reasons_reject_canonical_placeholder_prose():
    with __import__("pytest").raises(ValueError):
        HypothesisTransition(hypothesis_id="H1", transition="keep", reason="How the evidence changed the state.")
    with __import__("pytest").raises(ValueError):
        UncertaintyTransition(uncertainty_id="H1:U1", transition="keep", reason="How the evidence changed the state.")
    with __import__("pytest").raises(ValueError):
        HypothesisTransition(hypothesis_id="H1", transition="other", reason="A reason.", requested_operation_name="reframe", requested_effect="Change framing.", why_existing_operations_do_not_fit={"keep": "How the evidence changed the state."})
    with __import__("pytest").raises(ValueError):
        UncertaintyTransition(uncertainty_id="H1:U1", transition="other", reason="A reason.", requested_operation_name="merge", requested_effect="Combine issues.", why_existing_operations_do_not_fit={"keep": "How the evidence changed the state."})


def test_other_transition_operation_reasons_must_be_substantive():
    value = {"hypothesis_id": "H1", "transition": "other", "reason": "A reason.", "requested_operation_name": "reframe", "requested_effect": "Change framing.", "why_existing_operations_do_not_fit": {"keep": ""}}
    result = evaluate_raw(json.dumps(value), HypothesisTransition)
    assert result.code is FailureCode.S4
    value["why_existing_operations_do_not_fit"] = {"keep": "REPLACE_WITH_REASON"}
    assert evaluate_raw(json.dumps(value), HypothesisTransition).code is FailureCode.S4
    uncertainty = {"uncertainty_id": "H1:U1", "transition": "other", "reason": "Need more", "requested_operation_name": "merge", "requested_effect": "Combine issues.", "why_existing_operations_do_not_fit": {"REPLACE_WITH_OPERATION": "It does not fit."}}
    assert evaluate_raw(json.dumps(uncertainty), UncertaintyTransition).code is FailureCode.S4


def test_model_screen_hypothesis_text_must_be_substantive():
    value = {"hypotheses": [{"statement": "A", "justification": "B", "uncertainty": "C"}, {"statement": "D", "justification": "E", "uncertainty": "F"}]}
    value["hypotheses"][0]["statement"] = ""
    result = evaluate_raw(json.dumps(value), HypothesisResponse)
    assert result.code is FailureCode.S4


def test_smoke_response_answer_must_be_substantive():
    result = evaluate_raw(json.dumps({"answer": ""}), SmokeResponse)
    assert result.code is FailureCode.S4
    placeholder = evaluate_raw(json.dumps({"answer": "REPLACE_WITH_SUBSTANTIVE_TEXT"}), SmokeResponse)
    assert placeholder.code is FailureCode.S4


def test_next_step_conclusion_reason_must_be_substantive():
    value = {"step_type": "conclusion", "conclusion_hypothesis_id": "H1", "conclusion_reason": ""}
    result = evaluate_raw(json.dumps(value), NextStepResponse)
    assert result.code is FailureCode.S4


def test_assurance_module_cli_reports_summary(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(["uv", "run", "python", "-m", "experiments.contract_assurance", "--root", str(root), "--output", str(tmp_path), "--commit", "abc"], cwd=root, capture_output=True, text=True, check=True, env={**__import__("os").environ, "UV_CACHE_DIR": "/tmp/codex_uv_contract_assurance"})
    assert "contracts=8" in completed.stdout and "unexpected_accepts=0" in completed.stdout


def test_mutations_preserve_provenance_and_deduplicate():
    items = mutations(valid_action(), required_fields=("selected_action_id",))
    assert items[0].raw_output == "" and items[0].intended_code == "S0"
    assert {item.name for item in items} >= {"invented_literal", "id_with_explanation", "placeholder_text", "wrong_primitive_selected_action_id"}
    assert len(deduplicate(items)) == len(items)


def test_expanded_serialization_and_shape_mutations_keep_boundary_codes():
    items = deduplicate(mutations(valid_action(), required_fields=("selected_action_id",)))
    by_name = {item.name: item for item in items}
    for name in {
        "truncated_json_midpoint",
        "trailing_comma",
        "duplicate_braces",
        "multiple_json_objects",
        "json_array_top_level",
        "json_null_top_level",
        "json_string_top_level",
        "json_number_top_level",
        "empty_fence",
        "broken_opening_fence",
        "broken_closing_fence",
    }:
        assert by_name[name].intended_code == "S0"
    assert evaluate_raw(by_name[name].raw_output, NextActionResponse).code is FailureCode.S0
    assert by_name["extra_wrapper"].intended_code == "S1"
    assert evaluate_raw(by_name["extra_wrapper"].raw_output, NextActionResponse).code is FailureCode.S1


def test_steward_schema_valid_unknown_operations_reach_coordinator_boundary():
    from experiments.contract_assurance.evaluate import evaluate_steward
    from experiments.contract_assurance.runner import _sample_for
    from experiments.contract_assurance.registry import contract_registry
    from experiments.steward_screen.scenarios import all_scenarios

    sample = _sample_for(contract_registry()["StewardDecisionResponse"].schema)
    scenario = all_scenarios()[0]
    cases = {item.name: item for item in mutations(sample, contract="StewardDecisionResponse") if item.name.startswith("unknown_")}
    assert set(cases) == {"unknown_generalize_target", "unknown_archive_target", "unknown_shift_destination", "unknown_stop_unresolved_id"}
    for item in cases.values():
        result = evaluate_steward(item.raw_output, scenario=scenario)
        assert not result.accepted and result.code is FailureCode.S4 and result.stage == "coordinator_preflight"


def test_contract_mutations_cover_nested_shape_and_cross_field_rules():
    expansion = {
        "seed_analysis": {"supported_by": ["E1"], "conflicted_by": [], "unresolved": ["Question"], "specificity_basis_evidence_ids": []},
        "competing_hypotheses": [{"id": "H2", "parent_id": None, "statement": "Different.", "status": "active", "supported_by": ["E2"], "conflicted_by": [], "unresolved": ["Question"], "specificity_basis_evidence_ids": [], "relationship": "competing_root", "contrasted_hypothesis_id": "H1", "material_difference": "Different cause."}],
        "selected_action_id": "A1", "target_uncertainty": "Question", "expected_information_value": "Value", "why_this_action_now": "Reason",
    }
    names = {item.name for item in mutations(expansion, required_fields=("competing_hypotheses",), contract="InitialExpansionResponse")}
    assert {"unexpected_nested_competing_hypotheses", "empty_required_competing_hypotheses", "competing_root_with_parent", "null_seed_analysis", "wrong_shape_seed_analysis", "unexpected_seed_analysis_field", "empty_seed_analysis_unresolved", "canonical_placeholder_seed_analysis_unresolved", "placeholder_expansion_statement", "placeholder_expansion_unresolved_question", "canonical_placeholder_expansion_unresolved_question", "placeholder_expansion_material_difference", "wrong_namespace_seed_analysis_supported_by", "wrong_namespace_seed_analysis_conflicted_by", "wrong_namespace_seed_analysis_specificity_basis_evidence_ids", "wrong_namespace_expansion_supported_by", "wrong_namespace_expansion_conflicted_by", "wrong_namespace_expansion_specificity_basis_evidence_ids", "competing_root_with_specialization_evidence"} <= names


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
    assert BlindBatchAudit("w1", "NextActionResponse", "abc", ("package.json",), True, False, isolation_evidence=("probe",)).qualifies_as_blind
    assert not BlindBatchAudit("w1", "NextActionResponse", "abc", ("package.json",), True, False).qualifies_as_blind
    assert not BlindBatchAudit("w2", "NextActionResponse", "abc", ("repo",), False, True).qualifies_as_blind


def test_blind_role_instructions_exist_without_validator_details():
    root = Path(__file__).resolve().parents[1] / "experiments/contract_assurance/blind"
    producer = (root / "producer.md").read_text()
    adversary = (root / "adversary.md").read_text()
    assert "supplied frozen public package" in producer
    assert "subtle outputs" in adversary
    assert "failure taxonomy" not in producer


def test_generated_results_and_secrets_are_gitignored():
    root = Path(__file__).resolve().parents[1]
    for relative in ("experiments/contract_assurance/results/probe/evaluations.json", "runs/probe.json", ".env"):
        result = subprocess.run(["git", "check-ignore", "--no-index", relative], cwd=root, capture_output=True, text=True)
        assert result.returncode == 0, relative


def test_all_committed_public_packages_match_registered_contracts():
    packages = Path(__file__).resolve().parents[1] / "experiments/contract_assurance/blind/packages"
    snapshots = Path(__file__).resolve().parents[1] / "experiments/contract_assurance/snapshots"
    assert (snapshots / "README.md").exists()
    registry = contract_registry()
    for path in packages.glob("*.json"):
        payload = json.loads(path.read_text())
        contract = payload["manifest"]["contract"]
        assert contract in registry
        assert verify_snapshot_against_contract(payload, registry[contract]) == [], path.name
    assert verify_committed_packages(Path(__file__).resolve().parents[1]) == []
    assert {path.stem for path in packages.glob("*.json")} == set(registry)


def test_evolution_records_require_semantic_and_regression_evidence():
    assert "legitimate_semantic_need" in validate_evolution_record({"change_id": "C1"})
    record = {field: "present" for field in ("change_id", "contract", "discovery_source", "baseline_failure", "classification", "legitimate_semantic_need", "why_existing_contract_insufficient", "implementation_change", "tests_added", "expected_reclassifications", "unexpected_regressions", "before_after_compliance", "commit")}
    assert validate_evolution_record(record) == []


def test_committed_evolution_records_are_complete():
    root = Path(__file__).resolve().parents[1] / "experiments/contract_assurance/evolution/records"
    records = list(root.glob("*.json"))
    assert records
    assert all(validate_evolution_record(json.loads(path.read_text(encoding="utf-8"))) == [] for path in records)
    complete = {field: "present" for field in ("change_id", "contract", "discovery_source", "baseline_failure", "classification", "legitimate_semantic_need", "why_existing_contract_insufficient", "implementation_change", "tests_added", "expected_reclassifications", "unexpected_regressions", "before_after_compliance")}
    assert "commit" in validate_evolution_record({**complete, "commit": "pending"})


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


def test_initial_availability_is_checked_before_state_construction():
    result = evaluate_initial(json.dumps(valid_action() | {"hypotheses": [{"id": "H1", "statement": "A", "status": "active", "supported_by": ["E1"], "conflicted_by": [], "unresolved": ["U"], "specificity_basis_evidence_ids": []}]}), schema=InitialResponse, build_state=lambda response: (_ for _ in ()).throw(AssertionError("state construction should not run")), available_action_ids={"A2"})
    assert not result.accepted and result.code is FailureCode.S3 and result.stage == "availability"


def test_revision_operation_preflight_rejects_unknown_reference():
    from investigator.environments.case_01 import Case1ControlledEnvironment
    from investigator.services.contracts import InitialResponse
    environment = Case1ControlledEnvironment(Path(__file__).resolve().parents[1] / "experiments/investigation_smoke/case_01/artifacts")
    state = environment.build_initial_state(InitialResponse.model_validate({"hypotheses": [{"id": "H1", "statement": "A", "status": "active", "supported_by": ["E1"], "conflicted_by": [], "unresolved": ["U"], "specificity_basis_evidence_ids": []}], **valid_action()}))
    raw = {"hypothesis_updates": [{"hypothesis_id": "H1", "transition": "keep", "reason": "r", "add_supporting_evidence_ids": ["E999"]}], "new_hypotheses": [], "uncertainty_updates": [], "new_uncertainties": [], "revision_rationale": "r"}
    result = evaluate_revision(json.dumps(raw), state)
    assert not result.accepted and result.code is FailureCode.S3


def test_new_uncertainty_id_must_match_declared_owner():
    with __import__("pytest").raises(ValueError, match="must belong"):
        NewUncertainty(id="H2:U1", hypothesis_id="H1", description="A new uncertainty.", basis_evidence_ids=["E1"])


def test_revision_wrong_namespace_evidence_is_s2_before_preflight():
    from investigator.environments.case_01 import Case1ControlledEnvironment
    from investigator.services.contracts import InitialResponse
    environment = Case1ControlledEnvironment(Path(__file__).resolve().parents[1] / "experiments/investigation_smoke/case_01/artifacts")
    state = environment.build_initial_state(InitialResponse.model_validate({"hypotheses": [{"id": "H1", "statement": "A", "status": "active", "supported_by": ["E1"], "conflicted_by": [], "unresolved": ["U"], "specificity_basis_evidence_ids": []}], **valid_action()}))
    raw = {"hypothesis_updates": [{"hypothesis_id": "H1", "transition": "keep", "reason": "r", "add_supporting_evidence_ids": ["H1"]}], "new_hypotheses": [], "uncertainty_updates": [], "new_uncertainties": [], "revision_rationale": "r"}
    result = evaluate_revision(json.dumps(raw), state)
    assert not result.accepted and result.code is FailureCode.S2


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
