"""Repository inventory for model-facing Pydantic response contracts."""

import ast
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .registry import ContractSpec, contract_registry


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


def inventory(root: Path, commit: str = "unknown") -> dict[str, Any]:
    specs = list(contract_registry().values())
    entries = []
    for spec in specs:
        source = root / spec.source
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest() if source.exists() else None
        entries.append({**asdict(spec), "schema": spec.schema.__name__, "schema_hash": hashlib.sha256(json.dumps(spec.schema.model_json_schema(), sort_keys=True).encode()).hexdigest(), "source_hash": source_hash})
    return {"git_commit": commit, "contracts": entries, "discovered_response_classes": discover_response_classes(root), "unregistered_response_classes": unregistered_response_classes(root)}


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
    return "\n".join(lines) + "\n"


def write_inventory_markdown(root: Path, destination: Path, commit: str = "unknown") -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    path = destination.with_suffix(".md")
    path.write_text(render_inventory_markdown(inventory(root, commit)), encoding="utf-8")
    return path
