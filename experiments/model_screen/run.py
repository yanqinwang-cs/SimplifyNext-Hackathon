import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Keep the documented `python experiments/model_screen/run.py` invocation usable.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from experiments.model_screen.cases import CASES, get_case, render_case
from experiments.model_screen.prompt import build_prompt
from experiments.model_screen.schemas import CaseResult, HypothesisResponse, RunSummary
from investigator.llm.base import ModelClient
from investigator.llm.bedrock import BedrockModelClient


def safe_model_name(model_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", model_id).strip("._") or "model"


def run_case(client: ModelClient, model_id: str, case_id: str) -> CaseResult:
    case_input = render_case(get_case(case_id))
    prompt = build_prompt(case_input)
    try:
        call = client.call(prompt, HypothesisResponse)
        return CaseResult(
            case_id=case_id,
            model_id=model_id,
            case_input=case_input,
            prompt=prompt,
            parsed_output=call.parsed,
            raw_model_output=call.raw_output,
            metadata=call.metadata,
            parse_success=True,
        )
    except Exception as exc:
        return CaseResult(
            case_id=case_id,
            model_id=model_id,
            case_input=case_input,
            prompt=prompt,
            parse_success=False,
            raw_model_output=getattr(exc, "raw_output", None),
            error_message=str(exc),
        )


def run(
    model_id: str | None,
    case_id: str | None,
    results_root: Path = Path("experiments/model_screen/results"),
    client: ModelClient | None = None,
) -> Path:
    load_dotenv()
    active_client = client or BedrockModelClient(model_id=model_id)
    resolved_model_id = model_id or getattr(active_client, "model_id", "unknown-model")
    selected_cases = [get_case(case_id)] if case_id else CASES
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = results_root / f"{safe_model_name(resolved_model_id)}_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=False)

    results = [run_case(active_client, resolved_model_id, case["case_id"]) for case in selected_cases]
    for result in results:
        (output_dir / f"{result.case_id}.json").write_text(
            result.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
    summary = RunSummary(
        model_id=resolved_model_id,
        result_directory=str(output_dir),
        case_ids=[result.case_id for result in results],
        successful_cases=sum(result.parse_success for result in results),
        failed_cases=sum(not result.parse_success for result in results),
    )
    (output_dir / "run_summary.json").write_text(summary.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return output_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one Bedrock model against the qualitative model screen.")
    parser.add_argument("--model", help="Bedrock model or inference-profile ID; defaults to BEDROCK_MODEL_ID")
    parser.add_argument("--case", choices=[case["case_id"] for case in CASES], help="Run one case instead of all five")
    args = parser.parse_args()
    print(run(args.model, args.case))


if __name__ == "__main__":
    main()
