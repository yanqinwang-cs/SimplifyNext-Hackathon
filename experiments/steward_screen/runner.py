import argparse
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from time import sleep
from typing import Any

from pydantic import RootModel

from investigator.llm import ModelParseError
from investigator.llm.bedrock import BedrockModelClient

from experiments.steward_screen.evaluate import evaluate_result
from experiments.steward_screen.models import MODEL_REGISTRY, ScreenResult
from experiments.steward_screen.prompt import build_prompt
from experiments.steward_screen.report import write_report
from experiments.steward_screen.scenarios import all_scenarios


class JsonObject(RootModel[dict[str, Any]]):
    """Provider-level JSON envelope; Steward schema validation happens afterward."""


def jobs(models: list[str], scenarios: list[str], repetitions: int) -> list[tuple[str, str, int]]:
    return [(model, scenario, repetition) for model in models for scenario in scenarios for repetition in range(1, repetitions + 1)]


def _call_one(model_name: str, scenario_id: str, repetition: int, scenarios_by_id: dict[str, Any], client: Any, max_retries: int = 2) -> ScreenResult:
    scenario = scenarios_by_id[scenario_id]
    prompt = build_prompt(scenario)
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            call_result = client.call(prompt, JsonObject)
            return evaluate_result(model_name, MODEL_REGISTRY[model_name].invocation_id, scenario, repetition, prompt, call_result, retry_count=attempt)
        except Exception as exc:
            last_error = exc
            if isinstance(exc, ModelParseError):
                return evaluate_result(model_name, MODEL_REGISTRY[model_name].invocation_id, scenario, repetition, prompt, error=exc, retry_count=attempt)
            if attempt < max_retries:
                sleep(0.25 * (2**attempt))
    return evaluate_result(model_name, MODEL_REGISTRY[model_name].invocation_id, scenario, repetition, prompt, error=last_error, retry_count=max_retries)


def run_live(models: list[str], scenario_ids: list[str], repetitions: int, max_concurrency: int) -> list[ScreenResult]:
    scenarios_by_id = {scenario.scenario_id: scenario for scenario in all_scenarios()}
    clients = {name: BedrockModelClient(model_id=MODEL_REGISTRY[name].invocation_id, region=MODEL_REGISTRY[name].region) for name in models}
    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=max_concurrency) as pool:
        futures = [loop.run_in_executor(pool, _call_one, model, scenario, repetition, scenarios_by_id, clients[model]) for model, scenario, repetition in jobs(models, scenario_ids, repetitions)]
        return loop.run_until_complete(asyncio.gather(*futures))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the fixed-state Case Steward model screen.")
    parser.add_argument("--models", nargs="*", choices=sorted(MODEL_REGISTRY), default=sorted(MODEL_REGISTRY))
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--scenario", "--scenarios", nargs="*", default=None)
    parser.add_argument("--max-concurrency", type=int, default=4)
    parser.add_argument("--output-dir", type=Path, default=Path("experiments/steward_screen/results"))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    scenarios = all_scenarios()
    selected = args.scenario or [scenario.scenario_id for scenario in scenarios]
    unknown = sorted(set(selected) - {scenario.scenario_id for scenario in scenarios})
    if unknown:
        raise SystemExit(f"Unknown scenario IDs: {', '.join(unknown)}")
    if args.repetitions < 1 or args.max_concurrency < 1:
        raise SystemExit("repetitions and max-concurrency must be positive")
    expanded = jobs(args.models, selected, args.repetitions)
    if args.dry_run:
        print(json.dumps({"models": args.models, "scenarios": selected, "repetitions": args.repetitions, "jobs": len(expanded), "aws_calls": 0}, indent=2))
        return
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results = run_live(args.models, selected, args.repetitions, args.max_concurrency)
    output_dir = args.output_dir / run_id
    write_report(results, output_dir)
    print(f"Wrote {len(results)} results to {output_dir}")


if __name__ == "__main__":
    main()
