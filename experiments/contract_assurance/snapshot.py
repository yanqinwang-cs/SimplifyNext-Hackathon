import hashlib
import json
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from .registry import ContractSpec

FORBIDDEN_PUBLIC_TERMS = ("validator", "adversarial fixture", "prior result", "hidden artifact", "expected answer", "failure taxonomy")


def fingerprint(value: Any) -> str:
    payload = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def write_snapshot(spec: ContractSpec, destination: Path, *, prompt: str, case_input: Any, template: Any = None, commit: str = "unknown") -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    schema = spec.schema.model_json_schema()
    package = {"manifest": {"contract": spec.name, "source": spec.source, "git_commit": commit, "created_at": datetime.now(UTC).isoformat(), "schema_hash": fingerprint(schema), "prompt_hash": fingerprint(prompt), "case_input_hash": fingerprint(case_input), "template_hash": fingerprint(template)}, "prompt": prompt, "case_input": case_input, "output_schema": schema, "template": template}
    path = destination / f"{spec.name}.json"
    path.write_text(json.dumps(package, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return path


def validate_public_package(package: dict[str, Any]) -> list[str]:
    """Return boundary violations; callers must refuse non-empty results."""
    serialized = json.dumps(package, sort_keys=True, default=str).lower()
    issues = [f"forbidden public-package term: {term}" for term in FORBIDDEN_PUBLIC_TERMS if term in serialized]
    for key in ("validator", "tests", "adversarial_fixtures", "prior_results", "hidden_environment"):
        if key in package:
            issues.append(f"forbidden public-package field: {key}")
    manifest = package.get("manifest", {})
    if not package.get("prompt") or "output_schema" not in package:
        issues.append("package is missing prompt or output_schema")
    if not manifest.get("contract") or not manifest.get("schema_hash"):
        issues.append("manifest is missing contract provenance")
    return issues
