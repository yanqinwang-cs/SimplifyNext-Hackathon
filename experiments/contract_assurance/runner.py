"""Run the deterministic assurance slice; no network or model calls."""

import json
from pathlib import Path
from typing import Any

from .evaluate import evaluate_initial, evaluate_next_action, evaluate_raw, evaluate_revision
from .inventory import assert_complete_inventory, assert_inventory_paths, inventory
from .lint import lint_contract
from .snapshot import verify_snapshot_against_contract
from .mutations import deduplicate, mutations, write_fixture_manifest
from .registry import contract_registry
from .report import coverage_ledger, deterministic_correctness, deterministic_correctness_by_contract, failure_rate_statistics, summarize, summarize_by_contract, write_history


def run_deterministic(root: Path, output_dir: Path, commit: str = "unknown") -> dict[str, Any]:
    assert_complete_inventory(root)
    assert_inventory_paths(root)
    package_issues = verify_committed_packages(root)
    if package_issues:
        raise ValueError("Frozen public package verification failed: " + "; ".join(package_issues))
    results = []
    fixture_dir = root / "experiments/contract_assurance/fixtures"
    revision_state = _revision_state(root)
    environment = _case1_environment(root)
    for name, spec in contract_registry().items():
        sample = _sample_for(spec.schema)
        if sample is None:
            continue
        required = tuple(name for name, field in spec.schema.model_fields.items() if field.is_required())
        write_fixture_manifest(fixture_dir / f"{name}.json", name, sample, required)
        for mutation in deduplicate(mutations(sample, required_fields=required, contract=name)):
            if name == "NextActionResponse":
                result = evaluate_next_action(mutation.raw_output, {"A1"})
            elif name == "RevisionResponse":
                result = evaluate_revision(mutation.raw_output, revision_state)
            elif name == "InitialResponse":
                result = evaluate_initial(mutation.raw_output, schema=spec.schema, build_state=environment.build_initial_state, available_action_ids={"A1"})
            elif name == "InitialExpansionResponse":
                result = evaluate_initial(
                    mutation.raw_output,
                    schema=spec.schema,
                    build_state=lambda response: environment.build_seeded_initial_state("A human-seeded explanation.", response),
                    available_action_ids={"A1"},
                )
            else:
                result = evaluate_raw(mutation.raw_output, spec.schema)
            result.details.update({"contract": name, "mutation": mutation.name, "intended_code": mutation.intended_code})
            results.append(result)
    summary = summarize(results)
    summary["unexpected_accepts"] = sum(item.accepted and item.details.get("intended_code") != "valid" for item in results)
    summary["unexpected_rejects"] = sum((not item.accepted) and item.details.get("intended_code") == "valid" for item in results)
    blind = blind_compliance_summary(root)
    limitations = ["S6 reasoning and semantic quality are not assessed by deterministic schema assurance.", "SmokeResponse is schema-sampled offline; its live Bedrock path is excluded from this offline cycle."]
    report = {"inventory": inventory(root, commit), "deterministic": summary, "deterministic_failure_rate": failure_rate_statistics(results), "deterministic_correctness": deterministic_correctness(results), "deterministic_correctness_by_contract": deterministic_correctness_by_contract(results), "deterministic_by_contract": summarize_by_contract(results), "coverage_ledger": coverage_ledger(results), "blind_results_included": False, "blind_compliance": blind, "human_review_required": summary.get("s5_candidates", 0) > 0, "semantic_limitations": limitations, "changes_made": ["Deterministic fixtures evaluated through registered production-path adapters.", "Qualified blind manifests and output metrics aggregated without admitting NOT_BLIND results."], "regressions": {"unexpected_accepts": summary["unexpected_accepts"], "unexpected_rejects": summary["unexpected_rejects"], "status": "clean" if not summary["unexpected_accepts"] and not summary["unexpected_rejects"] else "regressions detected"}, "remaining_risks": ["S6 reasoning and semantic quality require a separate semantic checker.", "SmokeResponse live Bedrock execution remains excluded by the no-AWS constraint.", "Historical NOT_BLIND batches remain excluded from blind compliance statistics."]}
    write_history(output_dir, report)
    (output_dir / "inventory.json").write_text(json.dumps(report["inventory"], indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return report


def blind_compliance_summary(root: Path) -> dict[str, Any]:
    """Summarize recorded producer/adversary batches without admitting unqualified results."""
    batches = 0
    excluded = 0
    qualified = 0
    qualified_evaluations = qualified_accepted = qualified_rejected = 0
    qualified_output_metrics = {"outputs": 0, "placeholder_copy": 0, "fence_usage": 0, "total_output_chars": 0, "average_output_chars": 0.0}
    role_template = {"batches": 0, "qualified": 0, "excluded_not_blind": 0, "recorded_evaluations": 0, "recorded_accepted": 0, "recorded_rejected": 0, "evaluations": 0, "accepted": 0, "rejected": 0, "failure_codes": {}}
    by_role = {"producer": {**role_template, "failure_codes": {}}, "adversary": {**role_template, "failure_codes": {}}}
    by_contract: dict[str, dict[str, int]] = {}
    for path in sorted((root / "experiments/contract_assurance/results").glob("**/batch_manifest.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        audits = payload.get("audits")
        if audits is None and payload.get("worker_id"):
            audits = [payload]
        if not isinstance(audits, list):
            continue
        batches += 1
        status = payload.get("status", payload.get("blind_status"))
        result_path = path.parent / "evaluations.json"
        try:
            batch_evaluations = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            batch_evaluations = None
        evaluation_count_matches = payload.get("evaluation_count") is None or (
            isinstance(batch_evaluations, list) and len(batch_evaluations) == payload.get("evaluation_count")
        )
        is_qualified = status == "BLIND" and all(bool(item.get("qualifies_as_blind")) for item in audits) and evaluation_count_matches
        qualified += int(is_qualified)
        excluded += int(not is_qualified)
        contracts = {str(item.get("contract", "unknown")) for item in audits}
        for contract in contracts:
            stats = by_contract.setdefault(contract, {"batches": 0, "qualified": 0, "excluded_not_blind": 0, "recorded_evaluations": 0, "recorded_accepted": 0, "recorded_rejected": 0, "evaluations": 0, "accepted": 0, "rejected": 0, "failure_codes": {}})
            stats["batches"] += 1
            stats["qualified"] += int(is_qualified)
            stats["excluded_not_blind"] += int(not is_qualified)
        for item in audits:
            worker_id = str(item.get("worker_id", "")).lower()
            role = "adversary" if "adversary" in worker_id else "producer" if "producer" in worker_id else None
            if role:
                by_role[role]["batches"] += 1
                by_role[role]["qualified"] += int(is_qualified)
                by_role[role]["excluded_not_blind"] += int(not is_qualified)
                evaluations = batch_evaluations if isinstance(batch_evaluations, list) else []
                if isinstance(evaluations, list):
                    recorded_accepted = sum(bool(item.get("accepted")) for item in evaluations if isinstance(item, dict))
                    recorded_rejected = sum(not bool(item.get("accepted")) for item in evaluations if isinstance(item, dict))
                    by_role[role]["recorded_evaluations"] += len(evaluations)
                    by_role[role]["recorded_accepted"] += recorded_accepted
                    by_role[role]["recorded_rejected"] += recorded_rejected
                    if is_qualified:
                        for evaluation in evaluations:
                            if isinstance(evaluation, dict):
                                raw = evaluation.get("raw_output", "")
                                raw_text = raw if isinstance(raw, str) else json.dumps(raw, sort_keys=True, default=str)
                                qualified_output_metrics["outputs"] += 1
                                qualified_output_metrics["placeholder_copy"] += int("REPLACE_WITH_" in raw_text)
                                qualified_output_metrics["fence_usage"] += int("```" in raw_text)
                                qualified_output_metrics["total_output_chars"] += len(raw_text)
                        codes = {str(item.get("code")) for item in evaluations if isinstance(item, dict) and item.get("code")}
                        for code in sorted(codes):
                            count = sum(1 for item in evaluations if isinstance(item, dict) and item.get("code") == code)
                            by_role[role]["failure_codes"][code] = by_role[role]["failure_codes"].get(code, 0) + count
                            for contract in contracts:
                                by_contract[contract]["failure_codes"][code] = by_contract[contract]["failure_codes"].get(code, 0) + count
                        by_role[role]["evaluations"] += len(evaluations)
                        by_role[role]["accepted"] += recorded_accepted
                        by_role[role]["rejected"] += recorded_rejected
                        qualified_evaluations += len(evaluations)
                        qualified_accepted += recorded_accepted
                        qualified_rejected += recorded_rejected
                    for contract in contracts:
                        by_contract[contract]["recorded_evaluations"] += len(evaluations)
                        by_contract[contract]["recorded_accepted"] += recorded_accepted
                        by_contract[contract]["recorded_rejected"] += recorded_rejected
                        if is_qualified:
                            by_contract[contract]["evaluations"] += len(evaluations)
                            by_contract[contract]["accepted"] += recorded_accepted
                            by_contract[contract]["rejected"] += recorded_rejected
    qualified_output_metrics["average_output_chars"] = qualified_output_metrics["total_output_chars"] / qualified_output_metrics["outputs"] if qualified_output_metrics["outputs"] else 0.0
    qualified_failure_codes = {code: sum(data["failure_codes"].get(code, 0) for data in by_role.values()) for code in sorted({code for data in by_role.values() for code in data["failure_codes"]})}
    return {"status": "BLIND" if batches and qualified == batches else "NOT_BLIND", "batches": batches, "qualified_batches": qualified, "excluded_not_blind": excluded, "qualified_evaluations": qualified_evaluations, "qualified_accepted": qualified_accepted, "qualified_rejected": qualified_rejected, "qualified_failure_codes": qualified_failure_codes, "qualified_output_metrics": qualified_output_metrics, "by_role": by_role, "by_contract": dict(sorted(by_contract.items()))}


def _revision_state(root: Path) -> Any:
    """Build the smallest real case-01 state needed by revision preflight."""
    from investigator.services.contracts import InitialResponse

    environment = _case1_environment(root)
    initial = _sample_for(InitialResponse)
    return environment.build_initial_state(InitialResponse.model_validate(initial))


def _case1_environment(root: Path) -> Any:
    from investigator.environments.case_01 import Case1ControlledEnvironment

    return Case1ControlledEnvironment(root / "experiments/investigation_smoke/case_01/artifacts")


def verify_committed_packages(root: Path) -> list[str]:
    package_dir = root / "experiments/contract_assurance/blind/packages"
    registry = contract_registry()
    issues: list[str] = []
    paths = sorted(package_dir.glob("*.json"))
    package_names = {path.stem for path in paths}
    for missing in sorted(set(registry) - package_names):
        issues.append(f"missing blind package adapter: {missing}.json")
    for extra in sorted(package_names - set(registry)):
        issues.append(f"blind package has no registered contract: {extra}.json")
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        spec = registry.get(payload.get("manifest", {}).get("contract"))
        if spec is None:
            issues.append(f"{path.name}: unknown contract")
            continue
        issues.extend(f"{path.name}: {issue}" for issue in verify_snapshot_against_contract(payload, spec))
        issues.extend(f"{path.name}: {issue.message}" for issue in lint_contract(spec, prompt=payload.get("prompt", ""), template=payload.get("template")))
    return issues


def _sample_for(schema: type[Any]) -> dict[str, Any] | None:
    if schema.__name__ == "SmokeResponse":
        return {"answer": "4"}
    if schema.__name__ == "NextStepResponse":
        return {"step_type": "action", "selected_action_id": "A1", "target_uncertainty": "An open question.", "expected_information_value": "The result can distinguish explanations.", "why_this_action_now": "This enquiry is available now.", "conclusion_hypothesis_id": None, "conclusion_reason": None, "remaining_uncertainty_ids": []}
    if schema.__name__ == "InitialResponse":
        return {"hypotheses": [{"id": "H1", "parent_id": None, "statement": "A broad explanation.", "status": "active", "supported_by": ["E1"], "conflicted_by": [], "unresolved": ["What evidence would distinguish alternatives?"], "specificity_basis_evidence_ids": []}], "selected_action_id": "A1", "target_uncertainty": "Whether the claimed event occurred.", "expected_information_value": "The result can distinguish explanations.", "why_this_action_now": "This enquiry is available and relevant."}
    if schema.__name__ == "InitialExpansionResponse":
        return {"seed_analysis": {"supported_by": ["E1"], "conflicted_by": [], "unresolved": ["What remains uncertain?"], "specificity_basis_evidence_ids": []}, "competing_hypotheses": [{"id": "H2", "parent_id": None, "statement": "A materially different explanation.", "status": "active", "supported_by": ["E2"], "conflicted_by": [], "unresolved": ["Which account is better supported?"], "specificity_basis_evidence_ids": [], "relationship": "competing_root", "contrasted_hypothesis_id": "H1", "material_difference": "It proposes a different cause."}], "selected_action_id": "A1", "target_uncertainty": "Whether the claimed event occurred.", "expected_information_value": "The result can distinguish explanations.", "why_this_action_now": "This enquiry is available and relevant."}
    if schema.__name__ == "RevisionResponse":
        return {"hypothesis_updates": [], "new_hypotheses": [], "uncertainty_updates": [], "new_uncertainties": [], "revision_rationale": "No state change is justified by this release."}
    if schema.__name__ == "NextActionResponse":
        return {"selected_action_id": "A1", "target_uncertainty": "An open question.", "expected_information_value": "It may distinguish explanations.", "why_this_action_now": "It is available now."}
    if schema.__name__ == "HypothesisResponse":
        return {"hypotheses": [{"statement": "A", "justification": "Because", "uncertainty": "Unknown"}, {"statement": "B", "justification": "Because", "uncertainty": "Unknown"}]}
    return None
