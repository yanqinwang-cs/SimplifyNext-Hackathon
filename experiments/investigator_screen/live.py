"""One-turn live Bedrock runner for the Investigator diagnostic screen."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from experiments.investigator_screen.evaluate import evaluate_payload
from experiments.investigator_screen.audit import observation_fingerprint
from experiments.investigator_screen.fixtures import all_fixtures
from experiments.steward_screen.models import MODEL_REGISTRY
from investigator.cycle import InvestigatorTurnResponse
from investigator.cycle_prompt import build_investigator_cycle_prompt
from investigator.llm import ModelCallResult, ModelParseError
from investigator.llm.bedrock import BedrockModelClient


DEFAULT_OUTPUT_DIR = Path("experiments/investigator_screen/results/live")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_json(value: Any) -> str:
    return _sha_bytes(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _schema_fingerprint(schema: Any) -> str:
    return _sha_json(schema)


def _git_sha(root: Path) -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def current_manifest(model_name: str | None = None) -> dict[str, Any]:
    root = Path(__file__).parents[2]
    fixtures = all_fixtures()
    prompt_hashes = {
        fixture.fixture_id: _sha_bytes(
            build_investigator_cycle_prompt(fixture.observation).encode("utf-8")
        )
        for fixture in fixtures
    }
    manifest: dict[str, Any] = {
        "suite": "investigator-one-turn-diagnostic",
        "suite_version": "current",
        "fixture_count": len(fixtures),
        "fixture_ids": [fixture.fixture_id for fixture in fixtures],
        "public_observation_fingerprints": {
            fixture.fixture_id: observation_fingerprint(fixture) for fixture in fixtures
        },
        "prompt_hashes": prompt_hashes,
        "schema_hash": _schema_fingerprint(TypeAdapter(InvestigatorTurnResponse).json_schema()),
        "evaluator_hash": _sha_bytes((root / "experiments/investigator_screen/evaluate.py").read_bytes()),
        "prompt_source_hash": _sha_bytes((root / "src/investigator/cycle_prompt.py").read_bytes()),
        "commit_sha": _git_sha(root),
    }
    if model_name is not None:
        spec = MODEL_REGISTRY[model_name]
        manifest.update({"model_name": model_name, "invocation_id": spec.invocation_id, "region": spec.region})
    return manifest


def _result_fields(result: Any) -> dict[str, Any]:
    return {
        "schema_valid": result.schema_valid,
        "production_applied": result.production_applied,
        "semantic_pass": result.semantic_pass,
        "failure_categories": result.failure_categories,
        "diagnostics": result.diagnostics,
        "manual_review_flags": result.manual_review_flags,
        "outcome": result.outcome,
        "next_step_type": result.next_step_type,
        "update_operations": result.update_operations,
    }


def _trace_for_failure(fixture_id: str, raw_output: Any, error: Exception, stage: str) -> dict[str, Any]:
    return {
        "fixture_id": fixture_id,
        "raw_output": raw_output,
        "parse_success": False,
        "schema_valid": False,
        "production_applied": False,
        "semantic_pass": False,
        "failure_categories": [stage.upper()],
        "diagnostics": [str(error)],
        "next_step_type": None,
        "update_operations": [],
        "failure_stage": stage,
        "error": str(error),
    }


def run_model(model_name: str, fixtures: list[Any], output_dir: Path) -> dict[str, Any]:
    spec = MODEL_REGISTRY[model_name]
    client = BedrockModelClient(model_id=spec.invocation_id, region=spec.region)
    traces: list[dict[str, Any]] = []
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for fixture in fixtures:
        prompt = build_investigator_cycle_prompt(fixture.observation)
        prompt_hash = _sha_bytes(prompt.encode("utf-8"))
        trace: dict[str, Any] = {
            "run_id": f"{run_id}:{model_name}:{fixture.fixture_id}",
            "model_name": model_name,
            "invocation_id": spec.invocation_id,
            "region": spec.region,
            "fixture_id": fixture.fixture_id,
            "prompt_hash": prompt_hash,
            "raw_output": None,
            "latency_seconds": None,
            "input_tokens": None,
            "output_tokens": None,
            "parse_success": False,
            "failure_stage": None,
            "error": None,
        }
        try:
            call: ModelCallResult = client.call(prompt, InvestigatorTurnResponse)
            trace.update({
                "raw_output": call.raw_output,
                "latency_seconds": call.metadata.latency_seconds,
                "input_tokens": call.metadata.input_tokens,
                "output_tokens": call.metadata.output_tokens,
                "parse_success": call.metadata.parse_success,
            })
            result = evaluate_payload(fixture, call.parsed.model_dump(mode="json"))
            trace.update(_result_fields(result))
            trace["failure_stage"] = "semantic_evaluator" if not result.semantic_pass else None
            trace["error"] = None
        except ModelParseError as exc:
            trace.update(_trace_for_failure(fixture.fixture_id, exc.raw_output, exc, "model_parse"))
        except Exception as exc:
            trace.update(_trace_for_failure(fixture.fixture_id, getattr(exc, "raw_output", None), exc, "model_call"))
        traces.append(trace)

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = current_manifest(model_name)
    manifest.update({"run_id": run_id, "selected_fixture_ids": [fixture.fixture_id for fixture in fixtures]})
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "raw_traces.jsonl").write_text("\n".join(json.dumps(trace, sort_keys=True) for trace in traces) + "\n", encoding="utf-8")
    return {"run_id": run_id, "manifest": output_dir / "manifest.json", "raw_traces": output_dir / "raw_traces.jsonl", "traces": traces}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one live Investigator model call per diagnostic fixture.")
    parser.add_argument("--models", nargs="+", choices=sorted(MODEL_REGISTRY), required=True)
    parser.add_argument("--fixtures", nargs="+", default=[fixture.fixture_id for fixture in all_fixtures()])
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> None:
    parser = _parser()
    args = parser.parse_args()
    fixture_map = {fixture.fixture_id: fixture for fixture in all_fixtures()}
    unknown = sorted(set(args.fixtures) - set(fixture_map))
    if unknown:
        parser.error(f"unknown fixtures: {unknown}; expected INV1-INV12")
    selected = [fixture_map[identifier] for identifier in args.fixtures]
    if args.dry_run:
        print(json.dumps({
            "dry_run": True,
            "models": [{"name": name, "invocation_id": MODEL_REGISTRY[name].invocation_id, "region": MODEL_REGISTRY[name].region} for name in args.models],
            "selected_fixtures": [fixture.fixture_id for fixture in selected],
            "output_dir": str(args.output_dir.resolve()),
            "provenance": current_manifest(),
            "aws_calls": 0,
        }, indent=2, sort_keys=True))
        return

    for model_name in args.models:
        run_root = args.output_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") / model_name.replace("/", "_")
        result = run_model(model_name, selected, run_root)
        print("Completed Investigator one-turn run.")
        print(f"\nModel: {model_name}")
        print(f"Fixtures: {', '.join(fixture.fixture_id for fixture in selected)}")
        print(f"Trajectories: {len(result['traces'])}\n")
        for trace in result["traces"]:
            if trace.get("semantic_pass"):
                outcome = "PASS"
            elif trace.get("failure_categories"):
                outcome = f"FAIL — {', '.join(trace['failure_categories'])}"
            else:
                outcome = "NEEDS_MANUAL_REVIEW"
            print(f"{trace['fixture_id']}: {outcome}")
        print(f"\nManifest:\n  {result['manifest'].resolve()}")
        print(f"\nRaw traces:\n  {result['raw_traces'].resolve()}")


if __name__ == "__main__":
    main()
