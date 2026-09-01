import hashlib
import json
import re
from copy import deepcopy
from typing import Any

from pydantic import TypeAdapter, ValidationError

from investigator.llm import ModelCallResult, ModelParseError
from investigator.roles import GraphInvestigationCoordinator, StewardDecision, StewardOperation

from experiments.steward_screen.models import ScreenResult, StewardScenario


DECISION_ADAPTER = TypeAdapter(StewardDecision)
OPERATIONS = {operation.value for operation in StewardOperation}
CASE_NORMALIZATIONS = {operation.upper(): operation for operation in OPERATIONS}


def _payload(raw_output: Any) -> tuple[Any, str | None]:
    if isinstance(raw_output, str):
        try:
            return json.loads(raw_output), None
        except json.JSONDecodeError:
            return None, "F0_INVALID_JSON"
    return raw_output, None


def _schema_failure(payload: Any, exc: Exception) -> tuple[str, bool]:
    if not isinstance(payload, dict):
        return "F6_WRONG_FIELD_TYPE", False
    if "operation" not in payload:
        return ("F5_WRONG_FIELD_NAME", False) if "decision" in payload else ("F1_MISSING_DISCRIMINATOR", False)
    operation = payload["operation"]
    if isinstance(operation, str) and operation in CASE_NORMALIZATIONS:
        normalized = dict(payload)
        normalized["operation"] = CASE_NORMALIZATIONS[operation]
        try:
            DECISION_ADAPTER.validate_python(normalized)
            return "F2_INVALID_OPERATION_ENUM", True
        except ValidationError:
            pass
    if "rationale" in payload:
        return "F5_WRONG_FIELD_NAME", False
    if isinstance(exc, ValidationError):
        errors = exc.errors()
        if any(error["type"] == "extra_forbidden" for error in errors):
            return "F4_EXTRA_FIELD", False
        if any(error["type"] == "missing" for error in errors):
            return "F3_MISSING_REQUIRED_FIELD", False
        if any("type" in error["type"] for error in errors):
            return "F6_WRONG_FIELD_TYPE", False
        if any(error["type"] == "union_tag_invalid" for error in errors):
            return "F2_INVALID_OPERATION_ENUM", False
    return "F8_UNION_BRANCH_MISMATCH", False


def _diagnostic(record: ScreenResult, scenario: StewardScenario, payload: Any, strict_error: Exception) -> None:
    code, recoverable = _schema_failure(payload, strict_error)
    record.schema_failure_code = code
    record.schema_recoverable = recoverable
    if not recoverable:
        return
    normalized = dict(payload)
    normalized["operation"] = CASE_NORMALIZATIONS[normalized["operation"]]
    decision = DECISION_ADAPTER.validate_python(normalized)
    record.diagnostic_operation = decision.operation
    record.diagnostic_target_node_id = getattr(decision, "target_node_id", None)
    record.diagnostic_destination_node_id = getattr(decision, "destination_node_id", None)
    record.diagnostic_operation_correct = decision.operation == scenario.expected_operation
    record.diagnostic_identifier_correct = record.diagnostic_target_node_id == scenario.expected_target_node_id and record.diagnostic_destination_node_id == scenario.expected_destination_node_id
    record.diagnostic_decision_correct = record.diagnostic_operation_correct and record.diagnostic_identifier_correct


def evaluate_result(model_name: str, invocation_id: str, scenario: StewardScenario, repetition: int, prompt: str, call_result: ModelCallResult | None = None, error: Exception | None = None, retry_count: int = 0) -> ScreenResult:
    record = ScreenResult(run_id=f"{model_name}:{scenario.scenario_id}:{repetition}", model_name=model_name, invocation_id=invocation_id, scenario_id=scenario.scenario_id, repetition=repetition, prompt_hash=hashlib.sha256(prompt.encode()).hexdigest(), raw_model_output=call_result.raw_output if call_result else getattr(error, "raw_output", None), expected_operation=scenario.expected_operation, expected_target_node_id=scenario.expected_target_node_id, expected_destination_node_id=scenario.expected_destination_node_id, retry_count=retry_count, error_message=str(error) if error else None, input_tokens=call_result.metadata.input_tokens if call_result else None, output_tokens=call_result.metadata.output_tokens if call_result else None, elapsed_seconds=call_result.metadata.latency_seconds if call_result else None)
    if error:
        record.error_category = "schema_parse" if isinstance(error, ModelParseError) else "model_call"
        if isinstance(error, ModelParseError):
            payload, json_code = _payload(error.raw_output)
            if json_code:
                record.schema_failure_code = json_code
            else:
                _diagnostic(record, scenario, payload, error)
        return record
    try:
        raw = call_result.parsed.root if call_result else None
        decision = DECISION_ADAPTER.validate_python(raw)
    except Exception as exc:
        record.error_category = "schema_parse"
        record.error_message = str(exc)
        payload, json_code = _payload(call_result.raw_output if call_result else raw)
        if json_code:
            record.schema_failure_code = json_code
        else:
            _diagnostic(record, scenario, payload, exc)
        return record
    record.schema_valid = True
    record.parsed_decision = decision.model_dump(mode="json")
    record.actual_operation = decision.operation
    record.actual_target_node_id = getattr(decision, "target_node_id", None)
    record.actual_destination_node_id = getattr(decision, "destination_node_id", None)
    record.operation_correct = decision.operation == scenario.expected_operation
    record.identifier_correct = record.actual_target_node_id == scenario.expected_target_node_id and record.actual_destination_node_id == scenario.expected_destination_node_id
    record.invented_identifier = any(identifier not in scenario.graph.nodes for identifier in [record.actual_target_node_id, record.actual_destination_node_id] if identifier)
    record.tool_action_mention = bool(re.search(r"\b(?:tool|action)\b", str(record.parsed_decision), re.IGNORECASE))
    record.diagnostic_operation = record.actual_operation
    record.diagnostic_target_node_id = record.actual_target_node_id
    record.diagnostic_destination_node_id = record.actual_destination_node_id
    record.diagnostic_operation_correct = record.operation_correct
    record.diagnostic_identifier_correct = record.identifier_correct
    record.diagnostic_decision_correct = record.operation_correct and record.identifier_correct
    try:
        coordinator = GraphInvestigationCoordinator(deepcopy(scenario.graph), scenario.focus.model_copy(deep=True))
        coordinator.review_with_steward(decision, review_context=scenario.review_context)
        record.coordinator_accepted = True
        state = coordinator.graph
        expected = scenario.expected_state
        record.post_state_correct = coordinator.focus.node_id == expected.focus_node_id and coordinator.stopped == expected.stopped and all(state.nodes[node_id].status.value == "archived" for node_id in expected.archived_node_ids) and all(state.nodes[node_id].status.value == "active" for node_id in expected.active_node_ids)
    except Exception as exc:
        record.error_category = "coordinator_rejection"
        record.error_message = str(exc)
    return record
