"""Repository inventory for model-facing Pydantic response contracts."""

import ast
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .registry import ContractSpec, contract_registry


DYNAMIC_STRUCTURED_BOUNDARIES = (
    {
        "path": "experiments/gate1/runner.py",
        "symbol": "ExperimentRunner.run",
        "reason": "output_schema is supplied by the caller; the concrete schema must be registered at its call site",
    },
)


def discover_response_classes(root: Path) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for path in sorted(root.rglob("*.py")):
        if any(part in {".venv", "__pycache__", "results"} for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or not node.name.endswith("Response"):
                continue
            if any(isinstance(base, ast.Name) and base.id == "BaseModel" for base in node.bases):
                found.append({"name": node.name, "source": str(path.relative_to(root))})
    return found


def unregistered_response_classes(root: Path) -> list[dict[str, str]]:
    registered = {spec.schema.__name__ for spec in contract_registry().values()}
    return [item for item in discover_response_classes(root) if item["name"] not in registered]


def assert_complete_inventory(root: Path) -> None:
    missing = unregistered_response_classes(root)
    if missing:
        rendered = ", ".join(f"{item['name']} ({item['source']})" for item in missing)
        raise AssertionError(f"Unregistered LLM-facing response schemas: {rendered}")


def assert_inventory_paths(root: Path) -> None:
    missing: list[str] = []
    for spec in contract_registry().values():
        for relative in (spec.source, *spec.prompt_sources):
            if not (root / relative).is_file():
                missing.append(f"{spec.name}: {relative}")
    if missing:
        raise AssertionError("Missing registered contract paths: " + ", ".join(missing))


def assert_dynamic_structured_boundaries(root: Path) -> None:
    missing: list[str] = []
    for boundary in DYNAMIC_STRUCTURED_BOUNDARIES:
        path = root / boundary["path"]
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            missing.append(f"{boundary['symbol']}: unreadable source")
            continue
        class_name, method_name = boundary["symbol"].split(".", 1)
        methods = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == method_name
            and any(isinstance(parent, ast.ClassDef) and parent.name == class_name for parent in ast.walk(tree) if isinstance(parent, ast.ClassDef) and node in parent.body)
        ]
        has_client_call = any(
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "call"
            for method in methods for node in ast.walk(method)
        )
        if not methods or not has_client_call:
            missing.append(f"{boundary['symbol']}: expected structured model-client call not found")
    if missing:
        raise AssertionError("Invalid dynamic structured-call inventory: " + "; ".join(missing))


def inventory(root: Path, commit: str = "unknown") -> dict[str, Any]:
    specs = list(contract_registry().values())
    entries = []
    for spec in specs:
        source = root / spec.source
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest() if source.exists() else None
        prompt_hashes = {prompt: hashlib.sha256((root / prompt).read_bytes()).hexdigest() for prompt in spec.prompt_sources if (root / prompt).exists()}
        template_path = root / "experiments/contract_assurance/fixtures" / f"{spec.name}.json"
        template_hash = None
        if template_path.exists():
            try:
                template_payload = json.loads(template_path.read_text(encoding="utf-8"))
                template_hash = hashlib.sha256(json.dumps(template_payload.get("canonical"), sort_keys=True).encode()).hexdigest()
            except (OSError, json.JSONDecodeError):
                template_hash = None
        entries.append({**asdict(spec), "schema": spec.schema.__name__, "schema_hash": hashlib.sha256(json.dumps(spec.schema.model_json_schema(), sort_keys=True).encode()).hexdigest(), "source_hash": source_hash, "prompt_hashes": prompt_hashes, "template_source": str(template_path.relative_to(root)), "template_hash": template_hash})
    return {"git_commit": commit, "contracts": entries, "dynamic_structured_boundaries": list(DYNAMIC_STRUCTURED_BOUNDARIES), "discovered_response_classes": discover_response_classes(root), "unregistered_response_classes": unregistered_response_classes(root)}


def write_inventory(root: Path, destination: Path, commit: str = "unknown") -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    path = destination.with_suffix(".json")
    path.write_text(json.dumps(inventory(root, commit), indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return path


def render_inventory_markdown(data: dict[str, Any]) -> str:
    lines = ["# LLM-facing contract inventory", "", f"- Git commit: `{data.get('git_commit', 'unknown')}`", "", "| Contract | Schema | Source | Production path | Schema hash |", "| --- | --- | --- | --- | --- |"]
    for item in data.get("contracts", []):
        lines.append(f"| `{item['name']}` | `{item['schema']}` | `{item['source']}` | {item['production_path']} | `{item['schema_hash'][:12]}` |")
    unregistered = data.get("unregistered_response_classes", [])
    lines += ["", f"Unregistered response classes: **{len(unregistered)}**"]
    for item in unregistered:
        lines.append(f"- `{item['name']}` in `{item['source']}`")
    dynamic = data.get("dynamic_structured_boundaries", [])
    lines += ["", f"Dynamic structured-call boundaries: **{len(dynamic)}**"]
    for item in dynamic:
        lines.append(f"- `{item['symbol']}` in `{item['path']}`: {item['reason']}")
    return "\n".join(lines) + "\n"


def write_inventory_markdown(root: Path, destination: Path, commit: str = "unknown") -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    path = destination.with_suffix(".md")
    path.write_text(render_inventory_markdown(inventory(root, commit)), encoding="utf-8")
    return path
