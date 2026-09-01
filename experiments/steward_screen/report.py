import json
from collections import defaultdict
from pathlib import Path

from experiments.steward_screen.models import RunSummary, ScreenResult


def aggregate(results: list[ScreenResult]) -> RunSummary:
    by_model = defaultdict(lambda: {"runs": 0, "fully_correct": 0, "schema_failures": 0, "wrong_operation": 0, "wrong_identifier": 0, "coordinator_rejection": 0, "wrong_post_state": 0})
    by_scenario = defaultdict(lambda: {"runs": 0, "fully_correct": 0})
    for result in results:
        model = by_model[result.model_name]
        model["runs"] += 1
        if not result.schema_valid:
            model["schema_failures"] += 1
        if result.schema_valid and not result.operation_correct:
            model["wrong_operation"] += 1
        if result.schema_valid and not result.identifier_correct:
            model["wrong_identifier"] += 1
        if result.schema_valid and not result.coordinator_accepted:
            model["coordinator_rejection"] += 1
        if result.schema_valid and result.coordinator_accepted and not result.post_state_correct:
            model["wrong_post_state"] += 1
        full = result.schema_valid and result.operation_correct and result.identifier_correct and result.coordinator_accepted and result.post_state_correct
        if full:
            model["fully_correct"] += 1
        by_scenario[result.scenario_id]["runs"] += 1
        by_scenario[result.scenario_id]["fully_correct"] += int(full)
    return RunSummary(by_model=dict(by_model), by_scenario=dict(by_scenario))


def write_report(results: list[ScreenResult], output_dir: Path) -> RunSummary:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "runs.jsonl").open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result.model_dump(mode="json"), sort_keys=True) + "\n")
    summary = aggregate(results)
    (output_dir / "summary.json").write_text(summary.model_dump_json(indent=2), encoding="utf-8")
    lines = ["# Steward screen summary", "", "| Model | Runs | Fully correct | Schema failures | Wrong operation | Wrong identifier | Coordinator rejection | Wrong post-state |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for model, values in sorted(summary.by_model.items()):
        lines.append("| " + model + " | " + " | ".join(str(values[key]) for key in ("runs", "fully_correct", "schema_failures", "wrong_operation", "wrong_identifier", "coordinator_rejection", "wrong_post_state")) + " |")
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary
