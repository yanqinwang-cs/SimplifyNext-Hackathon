from dataclasses import dataclass
from typing import Any

from .registry import ContractSpec


@dataclass(frozen=True)
class LintIssue:
    contract: str
    message: str


def lint_contract(spec: ContractSpec, prompt: str = "", template: Any = None) -> list[LintIssue]:
    issues: list[LintIssue] = []
    fields = set(spec.schema.model_fields)
    if template is not None and isinstance(template, dict):
        unknown = set(template) - fields
        missing = {name for name, field in spec.schema.model_fields.items() if field.is_required()} - set(template)
        issues.extend(LintIssue(spec.name, f"template contains forbidden field {name!r}") for name in sorted(unknown))
        issues.extend(LintIssue(spec.name, f"template omits required field {name!r}") for name in sorted(missing))
    if "REPLACE_WITH_" in prompt and not any("REPLACE_WITH_" in value for value in spec.template_placeholders):
        issues.append(LintIssue(spec.name, "prompt contains an unregistered placeholder sentinel"))
    return issues
