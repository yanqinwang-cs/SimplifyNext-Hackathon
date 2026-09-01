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
    schema = spec.schema.model_json_schema()
    fields = set(spec.schema.model_fields)
    if not fields and schema.get("oneOf"):
        fields = {name for branch in schema["oneOf"] for name in _resolve_schema(branch, schema.get("$defs", {})).get("properties", {})}
    if getattr(spec.schema, "model_config", {}).get("extra") != "forbid":
        issues.append(LintIssue(spec.name, "public response schema must set extra='forbid'"))
    if template is not None and isinstance(template, dict):
        unknown = set(template) - fields
        missing = {name for name, field in spec.schema.model_fields.items() if field.is_required()} - set(template)
        issues.extend(LintIssue(spec.name, f"template contains forbidden field {name!r}") for name in sorted(unknown))
        issues.extend(LintIssue(spec.name, f"template omits required field {name!r}") for name in sorted(missing))
        values = _strings(template)
        issues.extend(LintIssue(spec.name, f"template contains canonical placeholder text {value!r}") for value in sorted(values & KNOWN_CANONICAL_PLACEHOLDERS))
        issues.extend(
            LintIssue(spec.name, f"template contains unregistered placeholder sentinel {value!r}")
            for value in sorted(values)
            if value.strip().startswith("REPLACE_WITH_")
            and not any(value.strip().startswith(prefix) for prefix in spec.template_placeholders)
        )
        issues.extend(_lint_nested_template(spec.name, schema, template, "", schema.get("$defs", {})))
    if "REPLACE_WITH_" in prompt and not any("REPLACE_WITH_" in value for value in spec.template_placeholders):
        issues.append(LintIssue(spec.name, "prompt contains an unregistered placeholder sentinel"))
    issues.extend(LintIssue(spec.name, f"prompt contains canonical placeholder text {value!r}") for value in sorted(KNOWN_CANONICAL_PLACEHOLDERS) if value in prompt)
    return issues


def _strings(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return {item for child in value.values() for item in _strings(child)}
    if isinstance(value, list):
        return {item for child in value for item in _strings(child)}
    return set()


def _lint_nested_template(contract: str, schema: dict[str, Any], template: Any, path: str, definitions: dict[str, Any]) -> list[LintIssue]:
    """Check required nested fields and non-empty collection examples from JSON schema metadata."""
    if not isinstance(template, dict):
        return []
    if schema.get("oneOf"):
        discriminator = schema.get("discriminator", {}).get("propertyName")
        value = template.get(discriminator) if discriminator else None
        selected = next((branch for branch in schema["oneOf"] if _resolve_schema(branch, definitions).get("properties", {}).get(discriminator, {}).get("const") == value), None)
        if selected is not None:
            schema = _resolve_schema(selected, definitions)
    reference = schema.get("$ref")
    if reference and reference.startswith("#/$defs/"):
        schema = definitions.get(reference.rsplit("/", 1)[-1], schema)
    properties = schema.get("properties", {})
    required = set(schema.get("required", ()))
    issues: list[LintIssue] = []
    for name in sorted(required - set(template)):
        location = f"{path}.{name}" if path else name
        issues.append(LintIssue(contract, f"template omits required field {location!r}"))
    for name, child in template.items():
        child_schema = properties.get(name, {})
        location = f"{path}.{name}" if path else name
        if isinstance(child, list):
            if child_schema.get("minItems", 0) and not child:
                issues.append(LintIssue(contract, f"template uses empty list for non-empty field {location!r}"))
            item_schema = child_schema.get("items", {})
            for index, item in enumerate(child):
                issues.extend(_lint_nested_template(contract, item_schema, item, f"{location}[{index}]", definitions))
        elif isinstance(child, dict):
            issues.extend(_lint_nested_template(contract, child_schema, child, location, definitions))
    return issues


def _resolve_schema(schema: dict[str, Any], definitions: dict[str, Any]) -> dict[str, Any]:
    reference = schema.get("$ref")
    if reference and reference.startswith("#/$defs/"):
        return definitions.get(reference.rsplit("/", 1)[-1], schema)
    return schema
