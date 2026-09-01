from dataclasses import dataclass, asdict
import json
from pathlib import Path
from typing import Any

from .snapshot import fingerprint, validate_public_package


def _disclosed_implementation_path(path: str) -> bool:
    """Treat self-reported implementation/test paths as contamination evidence."""
    normalized = path.replace("\\", "/").lower().strip("/")
    parts = set(normalized.split("/"))
    return bool(parts & {"src", "tests", "test", ".git", "implementation", "contract_assurance", "results"}) or normalized in {"repo", "repository", "worktree"}


@dataclass(frozen=True)
class BlindBatchAudit:
    worker_id: str
    contract: str
    package_hash: str
    provided_files: tuple[str, ...]
    repository_access_disabled: bool
    implementation_access_available: bool
    contamination_risks: tuple[str, ...] = ()
    isolation_evidence: tuple[str, ...] = ()
    input_family: str = "unspecified"

    @property
    def qualifies_as_blind(self) -> bool:
        disclosed_paths = tuple(path for path in self.provided_files if _disclosed_implementation_path(path))
        return self.repository_access_disabled and not self.implementation_access_available and not self.contamination_risks and not disclosed_paths and bool(self.isolation_evidence)

    def manifest(self) -> dict[str, Any]:
        family = self.input_family.strip() or "unspecified"
        return {**asdict(self), "input_family": family, "qualifies_as_blind": self.qualifies_as_blind}


def audit_public_package(package: dict[str, Any]) -> dict[str, Any]:
    issues = validate_public_package(package)
    return {"accepted": not issues, "issues": issues}


def audit_batch_package(package_path: str | Path, audit: BlindBatchAudit) -> dict[str, Any]:
    """Verify that a worker batch names the exact frozen public package it received."""
    path = Path(package_path)
    issues: list[str] = []
    try:
        package = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"accepted": False, "issues": [f"unreadable package: {exc}"]}
    issues.extend(audit_public_package(package)["issues"])
    if package.get("manifest", {}).get("contract") != audit.contract:
        issues.append("batch contract does not match package contract")
    if fingerprint(package) != audit.package_hash:
        issues.append("batch package hash mismatch")
    disclosed_paths = [path for path in audit.provided_files if _disclosed_implementation_path(path)]
    if disclosed_paths:
        issues.append("provided files disclose implementation or repository paths: " + ", ".join(disclosed_paths))
    if not audit.qualifies_as_blind:
        issues.append("batch is NOT_BLIND")
    return {"accepted": not issues, "issues": issues}


def record_batch(destination: str | Path, audit: BlindBatchAudit, evaluations: list[Any]) -> Path:
    """Persist exact worker evaluations and an immutable audit manifest."""
    from .evaluate import persist_evaluations

    path = Path(destination)
    path.mkdir(parents=True, exist_ok=True)
    persist_evaluations(evaluations, path / "evaluations.json")
    manifest = audit.manifest()
    manifest["evaluation_count"] = len(evaluations)
    manifest["blind_status"] = "BLIND" if audit.qualifies_as_blind else "NOT_BLIND"
    (path / "batch_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
