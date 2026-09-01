import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from investigator.llm.base import normalize_json_text
from .taxonomy import FailureCode
from investigator.state.operations import apply_revision


def _validation_code(exc: ValidationError) -> FailureCode:
    errors = exc.errors()
    messages = [str(error.get("msg", "")) for error in errors]
    error_types = [str(error.get("type", "")) for error in errors]
    if any("requires" in message or "must belong" in message or "only valid" in message or "cannot contain" in message or "Substantive text" in message or "Template placeholder" in message for message in messages):
        return FailureCode.S4
    if any("literal_error" in kind or "pattern" in kind or "enum" in kind for kind in error_types):
        return FailureCode.S2
    return FailureCode.S1


@dataclass
class Evaluation:
    accepted: bool
    code: FailureCode | None = None
    stage: str = ""
    message: str = ""
    parsed: Any = None
    raw_output: Any = None
    state_mutated: bool = False
    details: dict[str, Any] = field(default_factory=dict)


def evaluate_raw(raw_output: Any, schema: type[Any]) -> Evaluation:
    if isinstance(raw_output, (bytes, bytearray)):
        return Evaluation(False, FailureCode.S0, "serialization", "binary output is not an accepted response medium", raw_output=raw_output)
    if not isinstance(raw_output, str):
        try:
            parsed = schema.model_validate(raw_output)
        except ValidationError as exc:
            return Evaluation(False, _validation_code(exc), "schema", str(exc), raw_output=raw_output)
        return Evaluation(True, stage="schema", parsed=parsed, raw_output=raw_output)
    if not raw_output.strip():
        return Evaluation(False, FailureCode.S0, "serialization", "empty model output", raw_output=raw_output)
    try:
        value = json.loads(normalize_json_text(raw_output))
    except json.JSONDecodeError as exc:
        return Evaluation(False, FailureCode.S0, "serialization", str(exc), raw_output=raw_output)
    if not isinstance(value, dict):
        return Evaluation(False, FailureCode.S0, "serialization", "top-level JSON value must be an object", raw_output=raw_output)
    try:
        parsed = schema.model_validate(value)
    except ValidationError as exc:
        return Evaluation(False, _validation_code(exc), "schema", str(exc), raw_output=raw_output)
    return Evaluation(True, stage="schema", parsed=parsed, raw_output=raw_output)


def evaluate_next_action(raw_output: Any, available_action_ids: set[str]) -> Evaluation:
    from investigator.services.contracts import NextActionResponse

    result = evaluate_raw(raw_output, NextActionResponse)
    if result.accepted and result.parsed.selected_action_id not in available_action_ids:
        result.accepted = False
        result.code = FailureCode.S3
        result.stage = "availability"
        result.message = f"Action is not currently available: {result.parsed.selected_action_id!r}"
    return result


def evaluate_revision(raw_output: Any, state: Any) -> Evaluation:
    from investigator.services.contracts import RevisionResponse

    result = evaluate_raw(raw_output, RevisionResponse)
    if not result.accepted:
        return result
    try:
        updated = apply_revision(state, result.parsed)
    except (KeyError, ValueError) as exc:
        result.accepted = False
        result.code = FailureCode.S3 if "Unknown" in str(exc) or "evidence" in str(exc) else FailureCode.S4
        result.stage = "operation_preflight"
        result.message = str(exc)
        return result
    result.details["would_mutate"] = updated != state
    return result


def evaluate_initial(raw_output: Any, *, schema: type[Any], build_state: Any, available_action_ids: set[str] | None = None) -> Evaluation:
    """Evaluate an initial response through schema validation and state construction."""
    result = evaluate_raw(raw_output, schema)
    if not result.accepted:
        return result
    if available_action_ids is not None and result.parsed.selected_action_id not in available_action_ids:
        result.accepted = False
        result.code = FailureCode.S3
        result.stage = "availability"
        result.message = f"Action is not currently available: {result.parsed.selected_action_id!r}"
        return result
    try:
        state = build_state(result.parsed)
    except (KeyError, ValueError) as exc:
        message = str(exc)
        result.accepted = False
        result.code = FailureCode.S3 if "Unknown" in message or "evidence" in message else FailureCode.S4
        result.stage = "state_operation_preflight"
        result.message = message
        return result
    result.details["would_mutate"] = True
    result.details["state_type"] = type(state).__name__
    return result


def persist_evaluations(evaluations: list[Evaluation], destination: Any) -> Any:
    """Persist exact raw outputs and classifications to a generated results file."""
    from pathlib import Path

    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [{"accepted": item.accepted, "code": item.code.value if item.code else None, "stage": item.stage, "message": item.message, "raw_output": item.raw_output, "state_mutated": item.state_mutated, "details": item.details} for item in evaluations]
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return path
