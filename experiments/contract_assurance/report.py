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
    return {"total": len(items), "accepted": sum(item.accepted for item in items), "rejected": sum(not item.accepted for item in items), "failure_codes": dict(counts), "s5_candidates": counts.get("S5", 0), "s6_limitations": counts.get("S6", 0)}


def summarize_by_contract(evaluations: Iterable[Evaluation]) -> dict[str, dict]:
    grouped: dict[str, list[Evaluation]] = {}
    for item in evaluations:
        grouped.setdefault(str(item.details.get("contract", "unassigned")), []).append(item)
    return {contract: summarize(items) for contract, items in sorted(grouped.items())}


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
    if summary.get("failure_codes"):
        lines += ["## Failure codes", ""]
        lines.extend(f"- `{code}`: {count}" for code, count in sorted(summary["failure_codes"].items()))
    blind = report.get("blind_compliance")
    if blind:
        lines += ["", "## Blind compliance", "", f"- Status: `{blind.get('status', 'NOT_BLIND')}`", f"- Batches: {blind.get('batches', 0)}", f"- Qualified batches: {blind.get('qualified_batches', 0)}", f"- Excluded as NOT_BLIND: {blind.get('excluded_not_blind', 0)}"]
        if blind.get("by_contract"):
            lines += ["", "| Blind contract | Batches | Qualified | Excluded |", "| --- | ---: | ---: | ---: |"]
            lines.extend(f"| `{contract}` | {data.get('batches', 0)} | {data.get('qualified', 0)} | {data.get('excluded_not_blind', 0)} |" for contract, data in sorted(blind["by_contract"].items()))
        if blind.get("by_role"):
            lines += ["", "| Blind role | Batches | Qualified evaluations | Accepted | Rejected |", "| --- | ---: | ---: | ---: | ---: |"]
            lines.extend(f"| `{role}` | {data.get('batches', 0)} | {data.get('evaluations', 0)} | {data.get('accepted', 0)} | {data.get('rejected', 0)} |" for role, data in sorted(blind["by_role"].items()))
        if blind.get("qualified_failure_codes"):
            lines.append(f"- Qualified blind failure codes: `{blind['qualified_failure_codes']}`")
    limitations = report.get("semantic_limitations", [])
    if limitations:
        lines += ["", "## Assurance limitations", ""]
        lines.extend(f"- {item}" for item in limitations)
    by_contract = report.get("deterministic_by_contract", {})
    if by_contract:
        lines += ["", "## By contract", "", "| Contract | Total | Accepted | Rejected |", "| --- | ---: | ---: | ---: |"]
        lines.extend(f"| `{contract}` | {data.get('total', 0)} | {data.get('accepted', 0)} | {data.get('rejected', 0)} |" for contract, data in sorted(by_contract.items()))
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
