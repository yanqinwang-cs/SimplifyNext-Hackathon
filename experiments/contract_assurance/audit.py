from dataclasses import dataclass, asdict
import json
from pathlib import Path
from typing import Any

from .snapshot import fingerprint, validate_public_package


@dataclass(frozen=True)
class BlindBatchAudit:
    worker_id: str
    contract: str
    package_hash: str
    provided_files: tuple[str, ...]
    repository_access_disabled: bool
    implementation_access_available: bool
    contamination_risks: tuple[str, ...] = ()

    @property
    def qualifies_as_blind(self) -> bool:
        return self.repository_access_disabled and not self.implementation_access_available and not self.contamination_risks

    def manifest(self) -> dict[str, Any]:
        return {**asdict(self), "qualifies_as_blind": self.qualifies_as_blind}


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
    if not audit.qualifies_as_blind:
        issues.append("batch is NOT_BLIND")
    return {"accepted": not issues, "issues": issues}
