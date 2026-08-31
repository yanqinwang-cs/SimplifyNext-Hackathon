import json
import math
from datetime import datetime, UTC
from collections import Counter
from pathlib import Path
from typing import Iterable

from .evaluate import Evaluation
from .audit import BlindBatchAudit


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
    if summary.get("failure_codes"):
        lines += ["## Failure codes", ""]
        lines.extend(f"- `{code}`: {count}" for code, count in sorted(summary["failure_codes"].items()))
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
