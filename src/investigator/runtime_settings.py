"""Thread-safe, process-local Runtime settings for the normal vNext product."""
from __future__ import annotations
import os
from datetime import datetime, timezone
from threading import RLock
from typing import Any
from investigator.model_registry import MODEL_REGISTRY, ModelSpec
from investigator.llm.bedrock import credential_status

APPROVED_MODELS = ("anthropic.claude-sonnet-4-5", "anthropic.claude-opus-4-5")
DEFAULT_MODEL = "anthropic.claude-sonnet-4-5"
ROLE_KEYS = ("investigator", "workspace_help")
_LABELS = {"anthropic.claude-sonnet-4-5": "Claude Sonnet 4.5", "anthropic.claude-opus-4-5": "Claude Opus 4.5"}
_lock = RLock()
_overrides: dict[str, str | None] = dict.fromkeys(ROLE_KEYS)
_help_last_used: dict[str, dict[str, Any]] = {}

class RuntimeSettingsError(ValueError):
    """A configured Runtime setting is not supported by the product."""

def _label(name: str) -> str:
    return _LABELS[name]

def _environment_model(role: str) -> str | None:
    configured = os.getenv("VNEXT_INVESTIGATOR_MODEL_ID" if role == "investigator" else "WORKSPACE_MODEL_ID")
    if not configured:
        return None
    for name in APPROVED_MODELS:
        if configured in {name, MODEL_REGISTRY[name].invocation_id}:
            return name
    raise RuntimeSettingsError(f"Unsupported {role} model configuration")

def _resolved_name(role: str) -> tuple[str, str]:
    with _lock:
        override = _overrides[role]
    if override:
        return override, "runtime_selection"
    configured = _environment_model(role)
    return (configured, "environment") if configured else (DEFAULT_MODEL, "default")

def effective_model(role: str) -> ModelSpec:
    if role not in ROLE_KEYS:
        raise RuntimeSettingsError(f"Unsupported Runtime role: {role}")
    return MODEL_REGISTRY[_resolved_name(role)[0]]

def model_source(role: str) -> str:
    if role not in ROLE_KEYS:
        raise RuntimeSettingsError(f"Unsupported Runtime role: {role}")
    return _resolved_name(role)[1]

def available_models() -> list[dict[str, str]]:
    return [{"model": name, "label": _label(name)} for name in APPROVED_MODELS]

def record_help_model_use(case_id: str, model: str, outcome: str, used_at: str | None = None) -> None:
    if model not in APPROVED_MODELS or outcome not in {"completed", "failed"}:
        return
    with _lock:
        _help_last_used[case_id] = {"model": model, "label": _label(model), "usedAt": used_at or datetime.now(timezone.utc).isoformat(), "outcome": outcome}

def clear_process_usage() -> None:
    with _lock:
        _help_last_used.clear()

def _latest_investigator_use(case_id: str | None, workflow: Any | None) -> tuple[dict[str, Any] | None, bool]:
    if not case_id or workflow is None:
        return None, False
    runs = workflow.get_runs(case_id)
    latest = runs[-1] if runs else None
    if not latest:
        return None, False
    model = latest.get("model")
    logical = next((name for name in APPROVED_MODELS if MODEL_REGISTRY[name].invocation_id == model or name == model), None)
    if logical:
        outcome = "completed" if latest.get("outcome_type") == "COMPLETED" else "failed"
        return {"model": logical, "label": _label(logical), "usedAt": latest.get("ended_at") or latest.get("started_at"), "outcome": outcome}, False
    return None, bool(latest.get("vnext_status") == "completed" and latest.get("model") is None)

def settings(*, case_id: str | None = None, workflow: Any | None = None) -> dict[str, Any]:
    with _lock:
        help_use = dict(_help_last_used[case_id]) if case_id in _help_last_used else None
    investigator_use, no_model_call = _latest_investigator_use(case_id, workflow)
    models: dict[str, Any] = {}
    for role in ROLE_KEYS:
        name, source = _resolved_name(role)
        public_role = "workspaceHelp" if role == "workspace_help" else role
        models[public_role] = {"effectiveModel": name, "effectiveLabel": _label(name), "source": source, "lastUsed": help_use if role == "workspace_help" else investigator_use}
        if role == "investigator":
            models[public_role]["noModelCallRequired"] = no_model_call
    status = credential_status()
    temporary = bool(status["override_active"])
    return {"aws": {"mode": "temporary_credentials" if temporary else "default_chain", "statusLabel": "Temporary AWS credentials loaded" if temporary else "Default AWS credential chain", "lastUpdatedAt": status["last_updated_at"], "region": status["region"]}, "models": models, "availableModels": available_models()}

def set_model_overrides(payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload) != set(ROLE_KEYS):
        raise RuntimeSettingsError("Both Investigator and Workspace Help models are required")
    values = {role: payload[role] for role in ROLE_KEYS}
    if any(not isinstance(value, str) or value not in APPROVED_MODELS for value in values.values()):
        raise RuntimeSettingsError("Only Claude Sonnet 4.5 and Claude Opus 4.5 are supported")
    with _lock:
        _overrides.update(values)
    return settings()

def reset_model_overrides() -> dict[str, Any]:
    with _lock:
        for role in ROLE_KEYS:
            _overrides[role] = None
    return settings()
