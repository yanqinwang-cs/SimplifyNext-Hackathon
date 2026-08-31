"""Offline repair of model-screen results wrapped in one Markdown JSON fence."""

import argparse
import json
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

# Keep the documented direct script invocation import-safe.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiments.model_screen.schemas import CaseResult, HypothesisResponse, RunSummary
from investigator.llm.base import normalize_json_text


def _write_json_atomically(path: Path, value: dict) -> None:
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, indent=2)
        handle.write("\n")
    temporary.replace(path)


def _parse_stored_output(raw_output: str) -> HypothesisResponse:
    return HypothesisResponse.model_validate(json.loads(normalize_json_text(raw_output)))


def reparse_results(result_directory: str | Path) -> RunSummary:
    directory = Path(result_directory)
    case_paths = sorted(directory.glob("case_*.json"))
    for path in case_paths:
        stored = json.loads(path.read_text(encoding="utf-8"))
        if stored.get("parse_success") is not False or not stored.get("raw_model_output"):
            continue
        try:
            parsed = _parse_stored_output(stored["raw_model_output"])
        except Exception:
            continue
        stored["parsed_output"] = parsed.model_dump(mode="json")
        stored["parse_success"] = True
        stored["error_message"] = None
        _write_json_atomically(path, stored)

    summary_path = directory / "run_summary.json"
    summary = RunSummary.model_validate_json(summary_path.read_text(encoding="utf-8"))
    results = [CaseResult.model_validate_json(path.read_text(encoding="utf-8")) for path in case_paths]
    summary.successful_cases = sum(result.parse_success for result in results)
    summary.failed_cases = sum(not result.parse_success for result in results)
    _write_json_atomically(summary_path, summary.model_dump(mode="json"))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Reparse failed model-screen results offline.")
    parser.add_argument("result_directory", type=Path)
    args = parser.parse_args()
    print(reparse_results(args.result_directory).model_dump_json(indent=2))


if __name__ == "__main__":
    main()
