"""Process-local operator settings for controlled model testing."""

import os
from threading import RLock
from typing import Any

from investigator.model_registry import MODEL_REGISTRY, ModelSpec

_lock = RLock()
_overrides: dict[str, str | None] = {"investigator": None, "workspace_help": None, "steward": None}

def _default(role: str) -> str:
    if role == "investigator":
        configured = os.getenv("VNEXT_INVESTIGATOR_MODEL_ID")
        if configured:
            for spec in MODEL_REGISTRY.values():
                if spec.name == configured or spec.invocation_id == configured:
                    return spec.name
        return "anthropic.claude-sonnet-4-5"
    if role == "workspace_help":
        configured = os.getenv("WORKSPACE_MODEL_ID")
        if configured:
            for spec in MODEL_REGISTRY.values():
                if spec.name == configured or spec.invocation_id == configured:
                    return spec.name
        return "anthropic.claude-haiku-4-5"
    return "anthropic.claude-opus-4-5"

def effective_model(role: str) -> ModelSpec:
    with _lock:
        name = _overrides[role] or _default(role)
    return MODEL_REGISTRY[name]

def available_models() -> list[dict[str, str]]:
    return [{"name": spec.name, "label": _label(spec.name)} for spec in MODEL_REGISTRY.values()]

def _label(name: str) -> str:
    labels = {"anthropic.claude-haiku-4-5": "Claude Haiku 4.5", "anthropic.claude-sonnet-4-5": "Claude Sonnet 4.5", "anthropic.claude-opus-4-5": "Claude Opus 4.5"}
    return labels.get(name, name)

def settings() -> dict[str, Any]:
    with _lock:
        overrides = dict(_overrides)
    return {"models": {role: {"effective": effective_model(role).name, "override": overrides[role], **({"active_in_vnext": False} if role == "steward" else {})} for role in overrides}, "available_models": available_models()}

def set_model_overrides(payload: dict[str, Any]) -> dict[str, Any]:
    allowed = set(_overrides)
    if set(payload) - allowed:
        raise ValueError("Unknown runtime model role")
    with _lock:
        for role, name in payload.items():
            if name is not None and (not isinstance(name, str) or name not in MODEL_REGISTRY):
                raise ValueError(f"Unknown model: {name!r}")
            _overrides[role] = name
    return settings()

def reset_model_overrides() -> dict[str, Any]:
    with _lock:
        for role in _overrides:
            _overrides[role] = None
    return settings()
