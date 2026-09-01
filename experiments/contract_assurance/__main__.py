"""Command-line entry point for the offline contract-assurance cycle."""

import argparse
from pathlib import Path

from .runner import run_deterministic


def main() -> int:
    parser = argparse.ArgumentParser(description="Run offline structured-contract assurance")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, default=Path("experiments/contract_assurance/reports"))
    parser.add_argument("--commit", default="unknown")
    args = parser.parse_args()
    report = run_deterministic(args.root, args.output, args.commit)
    summary = report["deterministic"]
    print(f"contracts={sum(item['public'] for item in report['inventory']['contracts'])} total={summary['total']} accepted={summary['accepted']} rejected={summary['rejected']} unexpected_accepts={summary['unexpected_accepts']} unexpected_rejects={summary['unexpected_rejects']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
