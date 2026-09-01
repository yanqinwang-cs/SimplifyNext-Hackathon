"""Serial live Bedrock runner for the frozen sequential Steward suite."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from pydantic import RootModel, TypeAdapter

from investigator.llm.bedrock import BedrockConfigurationError, BedrockModelClient
from investigator.llm import ModelParseError
from investigator.roles import StewardDecision
from investigator.schema_fingerprint import schema_fingerprint
from experiments.steward_screen.fresh_fixtures import HISTORICAL_SUITE_HASH, SUITE_VERSION, fresh_fixtures
from experiments.steward_screen.prompt import build_prompt
from experiments.steward_screen.trajectory import run_fixture


class JsonObject(RootModel[dict[str, Any]]):
    """Provider envelope; the trajectory evaluator performs Steward validation."""


HISTORICAL_SCHEMA_HASH = "d51e77d4478ddd8d8d67517a3844776acc2809fcb92c99d5d428f419f74b4eae"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def frozen_manifest() -> dict[str, Any]:
    root = Path(__file__).parents[2]
    fixtures = fresh_fixtures()
    fixture_payload = json.dumps([f.model_dump(mode="json") for f in fixtures], sort_keys=True, separators=(",", ":")).encode()
    return {
        "suite_version": SUITE_VERSION,
        "fixture_suite_hash": _sha(fixture_payload),
        "historical_fixture_suite_hash": HISTORICAL_SUITE_HASH,
        "historical_fixture_status": "INVALID_FOR_MODEL_COMPARISON_AFTER_EPISTEMIC_FIXTURE_AUDIT",
        "schema_hash": schema_fingerprint(TypeAdapter(StewardDecision).json_schema()),
        "historical_schema_hash": HISTORICAL_SCHEMA_HASH,
        "historical_schema_hash_method": "sha256(json.dumps(StewardDecision.__metadata__, sort_keys=True, default=str).encode())",
        "schema_hash_method": "sha256(json.dumps(TypeAdapter(StewardDecision).json_schema(), sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8'))",
        "evaluator_hash": _sha((root / "experiments/steward_screen/trajectory.py").read_bytes()),
        "prompt_source_hash": _sha((root / "experiments/steward_screen/prompt.py").read_bytes()),
        "rendered_prompt_hash_seq7": _sha(build_prompt(fixtures[6]).encode()),
        "fixture_count": len(fixtures),
        "step_caps": {f.fixture_id: f.step_cap for f in fixtures},
    }


class LiveProducer:
    def __init__(self, client: BedrockModelClient):
        self.client = client
        self.calls: list[dict[str, Any]] = []

    def __call__(self, prompt: str) -> Any:
        prompt_hash = _sha(prompt.encode())
        try:
            call = self.client.call(prompt, JsonObject)
        except Exception as exc:
            self.calls.append({
                "prompt_hash": prompt_hash,
                "raw_output": getattr(exc, "raw_output", None),
                "latency_seconds": None,
                "input_tokens": None,
                "output_tokens": None,
                "stop_reason": None,
                "parse_success": False,
                "error": str(exc),
            })
            raise
        self.calls.append({
            "prompt_hash": prompt_hash,
            "raw_output": call.raw_output,
            "latency_seconds": call.metadata.latency_seconds,
            "input_tokens": call.metadata.input_tokens,
            "output_tokens": call.metadata.output_tokens,
            "stop_reason": call.metadata.finish_reason,
            "parse_success": call.metadata.parse_success,
        })
        return call.raw_output


def run_live(model_name: str, fixture_ids: list[str], output_dir: Path) -> dict[str, Any]:
    from experiments.steward_screen.models import MODEL_REGISTRY
    spec = MODEL_REGISTRY[model_name]
    client = BedrockModelClient(model_id=spec.invocation_id, region=spec.region)
    fixtures = {f.fixture_id: f for f in fresh_fixtures()}
    traces = []
    for fixture_id in fixture_ids:
        producer = LiveProducer(client)
        try:
            result = run_fixture(fixtures[fixture_id], producer)
            result_payload = result.__dict__
            failure_stage = None
            error = None
        except ModelParseError as exc:
            result_payload = None
            failure_stage = "model_parse"
            error = str(exc)
        except Exception as exc:
            result_payload = None
            failure_stage = "model_call"
            error = str(exc)
        traces.append({"run_id": f"{model_name}:{fixture_id}", "model_name": model_name, "invocation_id": spec.invocation_id, "region": spec.region, "fixture_id": fixture_id, "result": result_payload, "failure_stage": failure_stage, "error": error, "calls": producer.calls})
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(json.dumps({**frozen_manifest(), "model_name": model_name}, indent=2, sort_keys=True), encoding="utf-8")
    (output_dir / "raw_traces.jsonl").write_text("\n".join(json.dumps(t, default=str, sort_keys=True) for t in traces) + "\n", encoding="utf-8")
    return {"model_name": model_name, "trajectories": len(traces), "traces": traces}


def main() -> None:
    from experiments.steward_screen.models import MODEL_REGISTRY
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", choices=sorted(MODEL_REGISTRY), required=True)
    parser.add_argument("--fixtures", nargs="+", default=[f.fixture_id for f in fresh_fixtures()])
    parser.add_argument("--output-dir", type=Path, default=Path("experiments/steward_screen/results/sequential_live"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    manifest = frozen_manifest()
    selected = set(args.fixtures)
    known = set(manifest["step_caps"])
    if not selected <= known: parser.error(f"unknown fixtures: {sorted(selected - known)}")
    max_calls = sum(manifest["step_caps"][f] for f in selected) * len(args.models)
    if args.dry_run:
        print(json.dumps({"models": args.models, "fixtures": sorted(selected), "frozen": manifest, "maximum_possible_calls": max_calls, "output_dir": str(args.output_dir), "aws_calls": 0}, indent=2, sort_keys=True))
        return
    if not (os.getenv("AWS_PROFILE") or os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("AWS_WEB_IDENTITY_TOKEN_FILE")):
        raise SystemExit("LIVE_BLOCKED_AWS_CONFIGURATION")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for model in args.models:
        try:
            run_live(model, sorted(selected), args.output_dir / run_id / model.replace("/", "_"))
        except BedrockConfigurationError as exc:
            raise SystemExit(f"provider configuration failure for {model}: {exc}") from exc


if __name__ == "__main__":
    main()
