"""Repository inventory for model-facing Pydantic response contracts."""

import ast
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
    return {"git_commit": commit, "contracts": [{**asdict(spec), "schema": spec.schema.__name__, "schema_hash": __import__("hashlib").sha256(json.dumps(spec.schema.model_json_schema(), sort_keys=True).encode()).hexdigest()} for spec in specs], "discovered_response_classes": discover_response_classes(root), "unregistered_response_classes": unregistered_response_classes(root)}


def write_inventory(root: Path, destination: Path, commit: str = "unknown") -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    path = destination.with_suffix(".json")
    path.write_text(json.dumps(inventory(root, commit), indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return path
