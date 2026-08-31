import json
from datetime import datetime, UTC
from collections import Counter
from pathlib import Path
from typing import Iterable

from .evaluate import Evaluation


def summarize(evaluations: Iterable[Evaluation]) -> dict:
    items = list(evaluations)
    counts = Counter(item.code.value for item in items if item.code)
    return {"total": len(items), "accepted": sum(item.accepted for item in items), "rejected": sum(not item.accepted for item in items), "failure_codes": dict(counts)}


def write_report(destination: Path, report: dict) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def render_markdown(report: dict) -> str:
    lines = ["# Contract assurance report", "", f"- Generated: {report.get('generated_at', 'unknown')}", f"- Contracts: {report.get('contracts', report.get('total', 0))}", ""]
    if "accepted" in report:
        lines += ["| Metric | Count |", "| --- | ---: |", f"| Total evaluations | {report.get('total', 0)} |", f"| Accepted | {report.get('accepted', 0)} |", f"| Rejected | {report.get('rejected', 0)} |", ""]
    if report.get("failure_codes"):
        lines += ["## Failure codes", ""]
        lines.extend(f"- `{code}`: {count}" for code, count in sorted(report["failure_codes"].items()))
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
