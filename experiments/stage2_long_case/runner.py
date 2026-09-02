from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from investigator.cycle import CycleStatus, InvestigatorCycleCoordinator, InvestigatorTurnResponse
from investigator.graph import GraphStatus, GraphNodeType
from investigator.llm import ModelClient, ModelParseError
from investigator.llm.bedrock import BedrockModelClient
from investigator.roles.steward import StewardDecision, StewardReviewContext
from experiments.contract_assurance.snapshot import fingerprint
from experiments.steward_screen.models import MODEL_REGISTRY

from .evaluator import evaluate_run
from .fixture import CASE_ID, HIDDEN, Stage2Fixture, fresh_fixtures, run_fixture
from .prompt import build_prompt, build_steward_prompt

MAX_MODEL_CALLS = 24
MAX_STEPS = 60
MAX_TENURE_TURNS = 4


def _hash(value: Any) -> str:
    return fingerprint(value)


def _context(coordinator: InvestigatorCycleCoordinator) -> StewardReviewContext:
    unresolved = sorted(n.id for n in coordinator.graph.nodes.values() if n.node_type is GraphNodeType.UNCERTAINTY and n.status is GraphStatus.ACTIVE)
    return StewardReviewContext(global_frontier_assessed=True, local_frontier_exhausted=True, local_exhaustion_required=False, active_unresolved_ids=unresolved, obvious_useful_region_remains=False)


class _CallFailure(RuntimeError):
    def __init__(self, message: str, attempts: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.attempts = attempts


def _call(client: ModelClient, prompt: str, schema: Any, remaining: int) -> tuple[Any, list[dict[str, Any]], int]:
    attempts: list[dict[str, Any]] = []
    for attempt in range(1, min(2, remaining) + 1):
        try:
            call = client.call(prompt if attempt == 1 else prompt + "\nReturn only a corrected response matching the exact production schema.", schema)
            attempts.append({"attempt": attempt, "raw_output": call.raw_output, "parse_success": True, "error": None, "input_tokens": call.metadata.input_tokens, "output_tokens": call.metadata.output_tokens, "latency_seconds": call.metadata.latency_seconds, "finish_reason": call.metadata.finish_reason})
            return call, attempts, attempt
        except ModelParseError as exc:
            attempts.append({"attempt": attempt, "raw_output": exc.raw_output, "parse_success": False, "error": str(exc), "input_tokens": None, "output_tokens": None, "latency_seconds": None, "finish_reason": None})
            if attempt == 2 or attempt >= remaining:
                raise _CallFailure(str(exc), attempts) from exc
    raise _CallFailure("model call failed", attempts)


def run_trajectory(fixture: Stage2Fixture, investigator: ModelClient, steward: ModelClient, *, max_model_calls: int = MAX_MODEL_CALLS, max_steps: int = MAX_STEPS) -> dict[str, Any]:
    fixture = run_fixture(fixture)
    coordinator = InvestigatorCycleCoordinator(fixture.graph.model_copy(deep=True), fixture.focus.model_copy(deep=True), max_turns_per_tenure=MAX_TENURE_TURNS)
    traces: list[dict[str, Any]] = []
    usage = {"Investigator": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "latency": 0.0}, "Steward": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "latency": 0.0}}
    termination = "ORCHESTRATION_LIMIT"
    for step in range(1, max_steps + 1):
        if coordinator.cycle.status is CycleStatus.STOPPED:
            termination = coordinator.cycle.termination_reason or "HANDOFF_TO_HUMAN"
            break
        actor = "investigator" if coordinator.cycle.status is CycleStatus.LOCAL_ACTIVE else "steward"
        before = coordinator.graph.model_dump(mode="json")
        prompt = build_prompt(coordinator.observation(), fixture) if actor == "investigator" else build_steward_prompt(coordinator.steward_snapshot(), _context(coordinator), fixture)
        trace: dict[str, Any] = {"step": step, "actor": actor, "prompt_hash": _hash(prompt), "raw_model_output": None, "model_attempts": [], "parsed_response": None, "graph_before": before, "failure_category": None, "error": None, "input_tokens": None, "output_tokens": None, "latency_seconds": None}
        if sum(item["calls"] for item in usage.values()) >= max_model_calls:
            termination = "BUDGET_EXHAUSTED"
            break
        try:
            client = investigator if actor == "investigator" else steward
            schema = InvestigatorTurnResponse if actor == "investigator" else type("StewardSchema", (), {"model_validate": classmethod(lambda cls, value: TypeAdapter(StewardDecision).validate_python(value)), "model_json_schema": classmethod(lambda cls: TypeAdapter(StewardDecision).json_schema())})
            call, attempts, calls = _call(client, prompt, schema, max_model_calls - sum(item["calls"] for item in usage.values()))
            usage["Investigator" if actor == "investigator" else "Steward"]["calls"] += calls
            trace["model_attempts"] = attempts
            trace["raw_model_output"] = call.raw_output
            trace["parsed_response"] = call.parsed.model_dump(mode="json")
            trace["input_tokens"] = call.metadata.input_tokens
            trace["output_tokens"] = call.metadata.output_tokens
            trace["latency_seconds"] = call.metadata.latency_seconds
            usage["Investigator" if actor == "investigator" else "Steward"]["input_tokens"] += call.metadata.input_tokens or 0
            usage["Investigator" if actor == "investigator" else "Steward"]["output_tokens"] += call.metadata.output_tokens or 0
            usage["Investigator" if actor == "investigator" else "Steward"]["latency"] += call.metadata.latency_seconds
            if actor == "investigator":
                coordinator.apply_turn(call.parsed)
            else:
                context = _context(coordinator)
                coordinator.apply_steward_decision(call.parsed, coordinator.cycle.case_revision, review_context=context if call.parsed.operation == "handoff_to_human" else None)
        except _CallFailure as exc:
            trace["failure_category"] = "INVESTIGATOR_SCHEMA" if actor == "investigator" else "STEWARD_SCHEMA"
            trace["error"] = str(exc)
            trace["model_attempts"] = exc.attempts
            trace["raw_model_output"] = exc.attempts[-1].get("raw_output") if exc.attempts else None
            usage["Investigator" if actor == "investigator" else "Steward"]["calls"] += len(exc.attempts)
            termination = "SCHEMA_FAILURE"
            traces.append(trace)
            break
        except Exception as exc:
            trace["failure_category"] = "MODEL_ERROR" if actor == "investigator" else "STEWARD_APPLY_FAILURE"
            trace["error"] = str(exc)
            termination = "MODEL_ERROR" if actor == "investigator" else "STEWARD_APPLY_FAILURE"
            traces.append(trace)
            break
        trace["graph_after"] = coordinator.graph.model_dump(mode="json")
        trace["focus_after"] = coordinator.focus.model_dump(mode="json")
        traces.append(trace)
    total_calls = sum(item["calls"] for item in usage.values())
    handoff = next((trace.get("parsed_response") for trace in traces if trace.get("parsed_response", {}).get("operation") == "handoff_to_human"), None)
    return {"case_id": fixture.case_id, "termination_reason": termination, "model_calls": total_calls, "orchestration_steps": len(traces), "total_input_tokens": sum(item["input_tokens"] for item in usage.values()), "total_output_tokens": sum(item["output_tokens"] for item in usage.values()), "retry_count": sum(max(0, len(trace.get("model_attempts", [])) - 1) for trace in traces), "final_focus": coordinator.focus.model_dump(mode="json"), "final_handoff_present": handoff is not None, "traces": traces, "final_case_state": {"graph": coordinator.graph.model_dump(mode="json"), "focus": coordinator.focus.model_dump(mode="json"), "cycle": coordinator.cycle.model_dump(mode="json")}, "model_usage": usage}


def manifest(fixture: Stage2Fixture, git_sha: str, model_id: str) -> dict[str, Any]:
    public = [{"evidence_id": item.evidence_id, "filename": item.filename, "content": item.content} for item in fixture.evidence]
    hidden = {path.name: _hash(path.read_bytes()) for path in sorted(HIDDEN.glob("*.md"))}
    return {"suite": "stage2a-long-case", "suite_version": "stage2a-long-case-v1", "case_id": fixture.case_id, "git_sha": git_sha, "models": {"investigator": model_id, "steward": model_id}, "region": "us-east-1", "max_model_calls": MAX_MODEL_CALLS, "max_orchestration_steps": MAX_STEPS, "max_investigator_turns_per_tenure": MAX_TENURE_TURNS, "public_fixture_hash": _hash(public), "hidden_hashes": hidden, "public_filename_to_evidence_id": {item.filename: item.evidence_id for item in fixture.evidence}, "investigator_schema_hash": _hash(InvestigatorTurnResponse.model_json_schema()), "steward_schema_hash": _hash(TypeAdapter(StewardDecision).json_schema()), "hidden_files_exposed_to_models": False}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Stage 2A long-case experiment")
    parser.add_argument("--models", nargs="*", choices=["anthropic.claude-opus-4-5"], default=["anthropic.claude-opus-4-5"])
    parser.add_argument("--fixtures", nargs="*", default=[CASE_ID])
    parser.add_argument("--output-dir", type=Path, default=Path("experiments/stage2_long_case/runs"))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    fixtures = {fixture.case_id: fixture for fixture in fresh_fixtures()}
    unknown = sorted(set(args.fixtures) - set(fixtures))
    if unknown:
        raise SystemExit(f"Unknown fixture IDs: {', '.join(unknown)}")
    if args.dry_run:
        print(json.dumps({"fixtures": args.fixtures, "models": args.models, "model_calls": 0, "max_model_calls": MAX_MODEL_CALLS, "max_steps": MAX_STEPS}, indent=2))
        return
    model_id = MODEL_REGISTRY[args.models[0]].invocation_id
    clients = (BedrockModelClient(model_id=model_id, region=MODEL_REGISTRY[args.models[0]].region), BedrockModelClient(model_id=model_id, region=MODEL_REGISTRY[args.models[0]].region))
    for fixture_id in args.fixtures:
        fixture = fixtures[fixture_id]
        git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + git_sha[:7]
        out = args.output_dir / run_id
        out.mkdir(parents=True, exist_ok=False)
        started = time.perf_counter()
        result = run_trajectory(fixture, *clients)
        result.update({"run_id": run_id, "git_sha": git_sha, "model_configuration": {"investigator": model_id, "steward": model_id}, "elapsed_wall_clock_seconds": time.perf_counter() - started})
        result["evaluation"] = evaluate_run(result, max_model_calls=MAX_MODEL_CALLS, max_steps=MAX_STEPS)
        result["trace_paths"] = {"raw_traces": "raw_traces.jsonl", "final_case_state": "final_case_state.json", "model_usage": "model_usage.json"}
        (out / "manifest.json").write_text(json.dumps(manifest(fixture, result["git_sha"], model_id), indent=2) + "\n")
        (out / "raw_traces.jsonl").write_text("".join(json.dumps(trace, sort_keys=True) + "\n" for trace in result["traces"]))
        (out / "run_result.json").write_text(json.dumps({k: v for k, v in result.items() if k not in {"traces", "final_case_state", "model_usage"}}, indent=2, default=str) + "\n")
        (out / "final_case_state.json").write_text(json.dumps(result["final_case_state"], indent=2) + "\n")
        (out / "model_usage.json").write_text(json.dumps(result["model_usage"], indent=2) + "\n")
        handoff = next((trace["parsed_response"] for trace in result["traces"] if trace.get("parsed_response", {}).get("operation") == "handoff_to_human"), None)
        if handoff:
            (out / "final_handoff.json").write_text(json.dumps(handoff, indent=2) + "\n")
            result["trace_paths"]["final_handoff"] = "final_handoff.json"
            (out / "run_result.json").write_text(json.dumps({k: v for k, v in result.items() if k not in {"traces", "final_case_state", "model_usage"}}, indent=2, default=str) + "\n")
        print(json.dumps({"run_id": run_id, "status": result["evaluation"]["status"], "model_calls": result["model_calls"]}, indent=2))


if __name__ == "__main__":
    main()
