"""Run a blind worker with macOS Seatbelt repository-read isolation."""

from __future__ import annotations

import platform
import subprocess
from pathlib import Path
from typing import Sequence


def _profile(repo_root: Path, package_dir: Path, output_dir: Path) -> str:
    """Deny repository reads while allowing the frozen package and output area."""
    repo = str(repo_root.resolve())
    package = str(package_dir.resolve())
    output = str(output_dir.resolve())
    return (
        "(version 1) (allow default) "
        f'(deny file-read* (subpath "{repo}")) '
        f'(allow file-read* (subpath "{package}")) '
        f'(allow file-write* (subpath "{output}"))'
    )


def run_isolated_worker(
    command: Sequence[str],
    *,
    repo_root: str | Path,
    package_dir: str | Path,
    output_dir: str | Path,
    timeout: float = 120.0,
) -> subprocess.CompletedProcess[str]:
    """Run a worker command with auditable repository-read denial.

    The caller must record the successful invocation and a denied repository-read
    probe in its batch manifest before claiming ``BLIND``. On non-macOS hosts,
    this intentionally raises instead of silently weakening the isolation claim.
    """
    if platform.system() != "Darwin":
        raise RuntimeError("package-only worker isolation requires macOS sandbox-exec")
    package_path = Path(package_dir).resolve()
    output_path = Path(output_dir).resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    profile = _profile(Path(repo_root), package_path, output_path)
    return subprocess.run(
        ["sandbox-exec", "-p", profile, *command],
        cwd=output_path,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
