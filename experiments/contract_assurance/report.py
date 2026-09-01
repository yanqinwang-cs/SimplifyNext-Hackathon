import json
import math
from datetime import datetime, UTC
from collections import Counter
from pathlib import Path
from typing import Iterable

from .evaluate import Evaluation
from .audit import BlindBatchAudit


def failure_signature(item: Evaluation) -> str:
    return "|".join((str(item.details.get("contract", "unassigned")), item.code.value if item.code else "accepted", item.stage, item.message.splitlines()[0][:160]))


def coverage_ledger(evaluations: Iterable[Evaluation]) -> dict[str, int]:
    signatures: Counter[str] = Counter(failure_signature(item) for item in evaluations if not item.accepted)
    return dict(sorted(signatures.items()))


def summarize(evaluations: Iterable[Evaluation]) -> dict:
    items = list(evaluations)
    counts = Counter(item.code.value for item in items if item.code)
    stages = Counter(item.stage or "unknown" for item in items)
    return {"total": len(items), "accepted": sum(item.accepted for item in items), "rejected": sum(not item.accepted for item in items), "failure_codes": dict(counts), "stage_counts": dict(sorted(stages.items())), "s5_candidates": counts.get("S5", 0), "s6_limitations": counts.get("S6", 0)}


def summarize_by_contract(evaluations: Iterable[Evaluation]) -> dict[str, dict]:
    grouped: dict[str, list[Evaluation]] = {}
    for item in evaluations:
        grouped.setdefault(str(item.details.get("contract", "unassigned")), []).append(item)
    return {contract: summarize(items) for contract, items in sorted(grouped.items())}


def deterministic_correctness(evaluations: Iterable[Evaluation]) -> dict[str, float | int]:
    """Measure valid-fixture acceptance and invalid-fixture rejection separately."""
    items = list(evaluations)
    valid = [item for item in items if item.details.get("intended_code") == "valid"]
    invalid = [item for item in items if item.details.get("intended_code") not in (None, "valid")]
    return {
        "valid_fixtures": len(valid),
        "valid_accepted": sum(item.accepted for item in valid),
        "valid_pass_rate": sum(item.accepted for item in valid) / len(valid) if valid else 0.0,
        "invalid_fixtures": len(invalid),
        "invalid_rejected": sum(not item.accepted for item in invalid),
        "invalid_rejection_rate": sum(not item.accepted for item in invalid) / len(invalid) if invalid else 0.0,
    }


def deterministic_correctness_by_contract(evaluations: Iterable[Evaluation]) -> dict[str, dict[str, float | int]]:
    grouped: dict[str, list[Evaluation]] = {}
    for item in evaluations:
        grouped.setdefault(str(item.details.get("contract", "unassigned")), []).append(item)
    return {contract: deterministic_correctness(items) for contract, items in sorted(grouped.items())}


def summarize_blind(evaluations: Iterable[Evaluation], audits: Iterable[BlindBatchAudit]) -> dict:
    qualified = {audit.worker_id for audit in audits if audit.qualifies_as_blind}
    usable = [item for item in evaluations if str(item.details.get("worker_id", "")) in qualified]
    summary = summarize(usable)
    summary["excluded_not_blind"] = sum(1 for item in evaluations if item not in usable)
    return summary


def summarize_blind_by_role(evaluations: Iterable[Evaluation], audits: Iterable[BlindBatchAudit]) -> dict[str, dict]:
    """Report producer and adversary outcomes independently after blind filtering."""
    items = list(evaluations)
    qualified = {audit.worker_id for audit in audits if audit.qualifies_as_blind}
    roles = {str(item.details.get("role", "unassigned")) for item in items}
    return {
        role: summarize(item for item in items if str(item.details.get("worker_id", "")) in qualified and item.details.get("role") == role)
        for role in sorted(roles)
    }


def failure_rate_statistics(evaluations: Iterable[Evaluation], confidence: float = 0.95) -> dict[str, float | int]:
    """Return compliance/failure statistics; these are not reasoning confidence scores."""
    items = list(evaluations)
    failures = sum(not item.accepted for item in items)
    total = len(items)
    rate = failures / total if total else 0.0
    z = 1.96 if confidence == 0.95 else 1.645 if confidence == 0.90 else 2.576
    upper = min(1.0, (failures + z * z / 2 + z * math.sqrt((failures * (total - failures) / total) + z * z / 4)) / (total + z * z)) if total else 1.0
    return {"total": total, "failures": failures, "observed_failure_rate": rate, "upper_failure_rate": upper, "confidence": confidence}


def write_report(destination: Path, report: dict) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def render_markdown(report: dict) -> str:
    summary = report.get("deterministic", report)
    lines = ["# Contract assurance report", "", f"- Generated: {report.get('generated_at', 'unknown')}", f"- Contracts: {len(report.get('inventory', {}).get('contracts', [])) or report.get('contracts', summary.get('total', 0))}", ""]
    if "accepted" in summary:
        lines += ["| Metric | Count |", "| --- | ---: |", f"| Total evaluations | {summary.get('total', 0)} |", f"| Accepted | {summary.get('accepted', 0)} |", f"| Rejected | {summary.get('rejected', 0)} |", f"| Unexpected accepts | {summary.get('unexpected_accepts', 0)} |", f"| Unexpected rejects | {summary.get('unexpected_rejects', 0)} |", f"| S5 candidates | {summary.get('s5_candidates', 0)} |", f"| S6 limitations | {summary.get('s6_limitations', 0)} |", ""]
        lines.append(f"- Human review required for S5 candidates: `{report.get('human_review_required', summary.get('s5_candidates', 0) > 0)}`")
        rate = report.get("deterministic_failure_rate")
        if rate:
            lines.append(f"- Observed deterministic failure rate: `{rate.get('observed_failure_rate', 0):.4f}`; upper 95% bound: `{rate.get('upper_failure_rate', 0):.4f}` (compliance statistic, not reasoning confidence).")
        correctness = report.get("deterministic_correctness")
        if correctness:
            lines.append(f"- Valid-fixture pass rate: `{correctness.get('valid_pass_rate', 0):.4f}`; invalid-fixture rejection rate: `{correctness.get('invalid_rejection_rate', 0):.4f}`.")
    if summary.get("failure_codes"):
        lines += ["## Failure codes", ""]
        lines.extend(f"- `{code}`: {count}" for code, count in sorted(summary["failure_codes"].items()))
    if summary.get("stage_counts"):
        lines += ["", "## Production-path stages", ""]
        lines.extend(f"- `{stage}`: {count}" for stage, count in sorted(summary["stage_counts"].items()))
    prompt_lint = report.get("prompt_lint")
    if prompt_lint:
        lines += ["", "## Prompt/schema/template lint", "", f"- Status: `{prompt_lint.get('status', 'unknown')}`", f"- Issues: {len(prompt_lint.get('issues', []))}"]
        lines.extend(f"- {issue}" for issue in prompt_lint.get("issues", []))
    for heading, key in (("Changes made", "changes_made"), ("Regressions", "regressions"), ("Remaining risks", "remaining_risks")):
        value = report.get(key)
        if value:
            lines += ["", f"## {heading}", ""]
            if isinstance(value, dict):
                lines.extend(f"- `{name}`: {detail}" for name, detail in sorted(value.items()))
            else:
                lines.extend(f"- {item}" for item in value)
    blind = report.get("blind_compliance")
    if blind:
        lines += ["", "## Blind compliance", "", f"- Status: `{blind.get('status', 'NOT_BLIND')}`", f"- Batches: {blind.get('batches', 0)}", f"- Qualified batches: {blind.get('qualified_batches', 0)}", f"- Excluded as NOT_BLIND: {blind.get('excluded_not_blind', 0)}"]
        if blind.get("by_contract"):
            lines += ["", "| Blind contract | Batches | Qualified | Excluded |", "| --- | ---: | ---: | ---: |"]
            lines.extend(f"| `{contract}` | {data.get('batches', 0)} | {data.get('qualified', 0)} | {data.get('excluded_not_blind', 0)} |" for contract, data in sorted(blind["by_contract"].items()))
        if blind.get("by_contract_role"):
            lines += ["", "| Blind contract/role | Batches | Qualified | Excluded | Evaluations | Accepted | Rejected |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
            for contract, roles in sorted(blind["by_contract_role"].items()):
                for role, data in sorted(roles.items()):
                    lines.append(f"| `{contract}` / `{role}` | {data.get('batches', 0)} | {data.get('qualified', 0)} | {data.get('excluded_not_blind', 0)} | {data.get('evaluations', 0)} | {data.get('accepted', 0)} | {data.get('rejected', 0)} |")
        if blind.get("by_role"):
            lines += ["", "| Blind role | Batches | Qualified evaluations | Accepted | Rejected |", "| --- | ---: | ---: | ---: | ---: |"]
            lines.extend(f"| `{role}` | {data.get('batches', 0)} | {data.get('evaluations', 0)} | {data.get('accepted', 0)} | {data.get('rejected', 0)} |" for role, data in sorted(blind["by_role"].items()))
        if blind.get("qualified_failure_codes"):
            lines.append(f"- Qualified blind failure codes: `{blind['qualified_failure_codes']}`")
        if blind.get("qualified_output_metrics"):
            metrics = blind["qualified_output_metrics"]
            lines.append(f"- Qualified blind output metrics: placeholder copies `{metrics.get('placeholder_copy', 0)}`, fenced outputs `{metrics.get('fence_usage', 0)}`, average length `{metrics.get('average_output_chars', 0):.1f}` characters.")
        if blind.get("coverage_gaps"):
            lines.append(f"- Blind coverage gaps requiring fresh batches: `{', '.join(blind['coverage_gaps'])}`")
    limitations = report.get("semantic_limitations", [])
    if limitations:
        lines += ["", "## Assurance limitations", ""]
        lines.extend(f"- {item}" for item in limitations)
    by_contract = report.get("deterministic_by_contract", {})
    if by_contract:
        correctness_by_contract = report.get("deterministic_correctness_by_contract", {})
        lines += ["", "## By contract", "", "| Contract | Total | Accepted | Rejected | Valid pass | Invalid reject | S0 | S1 | S2 | S3 | S4 |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for contract, data in sorted(by_contract.items()):
            correctness = correctness_by_contract.get(contract, {})
            codes = data.get("failure_codes", {})
            lines.append(f"| `{contract}` | {data.get('total', 0)} | {data.get('accepted', 0)} | {data.get('rejected', 0)} | {correctness.get('valid_pass_rate', 0):.4f} | {correctness.get('invalid_rejection_rate', 0):.4f} | " + " | ".join(str(codes.get(code, 0)) for code in ("S0", "S1", "S2", "S3", "S4")) + " |")
    inventory_contracts = report.get("inventory", {}).get("contracts", [])
    if inventory_contracts:
        lines += ["", "## Contract provenance", "", "| Contract | Production path | Schema hash | Prompt hash(es) | Template hash |", "| --- | --- | --- | --- | --- |"]
        for contract in sorted(inventory_contracts, key=lambda item: item.get("name", "")):
            prompt_hashes = ", ".join(f"{source}: {value}" for source, value in sorted(contract.get("prompt_hashes", {}).items())) or "none"
            lines.append(f"| `{contract.get('name', '')}` | {contract.get('production_path', 'unknown')} | `{contract.get('schema_hash', 'unknown')}` | {prompt_hashes} | `{contract.get('template_hash', 'unknown')}` |")
    return "\n".join(lines) + "\n"


def write_history(destination: Path, report: dict, *, timestamp: str | None = None) -> tuple[Path, Path]:
    """Write latest JSON/Markdown and an immutable dated JSON history record."""
    report = {"generated_at": timestamp or datetime.now(UTC).isoformat(), **report}
    destination.mkdir(parents=True, exist_ok=True)
    latest_json = destination / "latest.json"
    latest_md = destination / "latest.md"
    latest_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    latest_md.write_text(render_markdown(report), encoding="utf-8")
    history = destination / "history"
    history.mkdir(exist_ok=True)
    stamp = report["generated_at"].replace(":", "").replace("+00:00", "Z").replace(".", "_")
    dated = history / f"{stamp}.json"
    dated.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return latest_json, dated
