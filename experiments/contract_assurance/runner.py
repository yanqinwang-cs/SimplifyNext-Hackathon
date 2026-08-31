"""Run the deterministic assurance slice; no network or model calls."""

import json
from pathlib import Path
from typing import Any

from .evaluate import evaluate_raw
from .inventory import inventory
from .mutations import deduplicate, mutations
from .registry import contract_registry
from .report import summarize, summarize_by_contract, write_history


def run_deterministic(root: Path, output_dir: Path, commit: str = "unknown") -> dict[str, Any]:
    results = []
    for name, spec in contract_registry().items():
        sample = _sample_for(spec.schema)
        if sample is None:
            continue
        for mutation in deduplicate(mutations(sample, required_fields=tuple(spec.schema.model_fields))):
            result = evaluate_raw(mutation.raw_output, spec.schema)
            result.details.update({"contract": name, "mutation": mutation.name, "intended_code": mutation.intended_code})
            results.append(result)
    summary = summarize(results)
    report = {"inventory": inventory(root, commit), "deterministic": summary, "deterministic_by_contract": summarize_by_contract(results), "blind_results_included": False}
    write_history(output_dir, summary)
    (output_dir / "inventory.json").write_text(json.dumps(report["inventory"], indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return report


def _sample_for(schema: type[Any]) -> dict[str, Any] | None:
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
