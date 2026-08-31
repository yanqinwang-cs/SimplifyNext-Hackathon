from dataclasses import dataclass
from typing import Any

from .registry import ContractSpec

KNOWN_CANONICAL_PLACEHOLDERS = {"The uncertainty this enquiry addresses.", "How the result could change the explanation space.", "Why this enquiry is useful now.", "How the evidence changed the state."}


@dataclass(frozen=True)
class LintIssue:
    contract: str
    message: str


def lint_contract(spec: ContractSpec, prompt: str = "", template: Any = None) -> list[LintIssue]:
    issues: list[LintIssue] = []
    fields = set(spec.schema.model_fields)
    if getattr(spec.schema, "model_config", {}).get("extra") != "forbid":
        issues.append(LintIssue(spec.name, "public response schema must set extra='forbid'"))
    if template is not None and isinstance(template, dict):
        unknown = set(template) - fields
        missing = {name for name, field in spec.schema.model_fields.items() if field.is_required()} - set(template)
        issues.extend(LintIssue(spec.name, f"template contains forbidden field {name!r}") for name in sorted(unknown))
        issues.extend(LintIssue(spec.name, f"template omits required field {name!r}") for name in sorted(missing))
        values = _strings(template)
        issues.extend(LintIssue(spec.name, f"template contains canonical placeholder text {value!r}") for value in sorted(values & KNOWN_CANONICAL_PLACEHOLDERS))
    if "REPLACE_WITH_" in prompt and not any("REPLACE_WITH_" in value for value in spec.template_placeholders):
        issues.append(LintIssue(spec.name, "prompt contains an unregistered placeholder sentinel"))
    return issues


def _strings(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return {item for child in value.values() for item in _strings(child)}
    if isinstance(value, list):
        return {item for child in value for item in _strings(child)}
    return set()
