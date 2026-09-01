"""Reproducible execution boundary for one isolated blind batch."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Sequence

from ..audit import BlindBatchAudit, record_batch
from ..snapshot import fingerprint
from .isolation import run_isolated_worker


def run_blind_batch(
    package_path: str | Path,
    destination: str | Path,
    *,
    command: Sequence[str],
    repo_root: str | Path,
    package_dir: str | Path,
    audit: BlindBatchAudit,
    evaluate: Callable[[str], Any],
) -> Path:
    """Stream exactly one frozen package to an isolated worker and record its output.

    The caller remains responsible for truthful ``BlindBatchAudit`` isolation
    evidence.  This helper never upgrades a batch to ``BLIND`` on its own.
    """
    package = Path(package_path)
    package_text = package.read_text(encoding="utf-8")
    package_payload = json.loads(package_text)  # Fail before execution if the supplied package is not JSON.
    if package_payload.get("manifest", {}).get("contract", package_payload.get("contract")) != audit.contract:
        raise ValueError("supplied package contract does not match blind audit")
    if fingerprint(package_payload) != audit.package_hash:
        raise ValueError("supplied package hash does not match blind audit")
    completed = run_isolated_worker(
        command,
        repo_root=repo_root,
        package_dir=package_dir,
        output_dir=destination,
        input_text=package_text,
    )
    evaluation = evaluate(completed.stdout)
    evaluation.details.update({"worker_returncode": completed.returncode, "worker_stderr": completed.stderr})
    return record_batch(destination, audit, [evaluation])
