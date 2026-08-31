from dataclasses import dataclass, asdict
from typing import Any

from .snapshot import validate_public_package


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
