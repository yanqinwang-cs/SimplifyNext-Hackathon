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
from .report import coverage_ledger, summarize, summarize_by_contract, write_history


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
    report = {"inventory": inventory(root, commit), "deterministic": summary, "deterministic_by_contract": summarize_by_contract(results), "coverage_ledger": coverage_ledger(results), "blind_results_included": False, "blind_compliance": blind_compliance_summary(root), "semantic_limitations": ["S6 reasoning and semantic quality are not assessed by deterministic schema assurance.", "SmokeResponse is schema-sampled offline; its live Bedrock path is excluded from this offline cycle."]}
    write_history(output_dir, report)
    (output_dir / "inventory.json").write_text(json.dumps(report["inventory"], indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return report


def blind_compliance_summary(root: Path) -> dict[str, Any]:
    """Summarize recorded producer/adversary batches without admitting unqualified results."""
    batches = 0
    excluded = 0
    qualified = 0
    by_role = {"producer": {"batches": 0, "qualified": 0, "excluded_not_blind": 0}, "adversary": {"batches": 0, "qualified": 0, "excluded_not_blind": 0}}
    by_contract: dict[str, dict[str, int]] = {}
    for path in sorted((root / "experiments/contract_assurance/results").glob("*/batch_manifest.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        audits = payload.get("audits", [])
        if not isinstance(audits, list):
            continue
        batches += 1
        is_qualified = payload.get("status") == "BLIND" and all(bool(item.get("qualifies_as_blind")) for item in audits)
        qualified += int(is_qualified)
        excluded += int(not is_qualified)
        contracts = {str(item.get("contract", "unknown")) for item in audits}
        for contract in contracts:
            stats = by_contract.setdefault(contract, {"batches": 0, "qualified": 0, "excluded_not_blind": 0})
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
    return {"status": "BLIND" if batches and qualified == batches else "NOT_BLIND", "batches": batches, "qualified_batches": qualified, "excluded_not_blind": excluded, "by_role": by_role, "by_contract": dict(sorted(by_contract.items()))}


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
    for path in sorted(package_dir.glob("*.json")):
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
