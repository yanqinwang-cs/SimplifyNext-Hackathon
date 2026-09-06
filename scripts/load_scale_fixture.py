#!/usr/bin/env python3
"""Load the controlled ten-candidate fixture as an ordinary case."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from investigator.state import CaseRepository
from investigator.test_fixtures import load_scale_fixture_case_state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture_root", type=Path)
    parser.add_argument("--repository", type=Path, default=Path("data/cases"))
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--title")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    repository = CaseRepository(args.repository)
    if repository.exists(args.case_id) and not args.replace:
        parser.error(f"case {args.case_id!r} already exists; pass --replace to replace only that case")
    state = load_scale_fixture_case_state(args.fixture_root).model_copy(update={"case_id": args.case_id})
    if args.title:
        state = state.model_copy(update={"title": args.title})
    if args.replace and repository.exists(args.case_id):
        repository.case_path(args.case_id).unlink()
        artifact_dir = repository.case_artifact_dir(args.case_id)
        if artifact_dir.exists():
            shutil.rmtree(artifact_dir)
    repository.save(state)
    print("Loaded scale fixture:")
    print(f"  case_id: {state.case_id}")
    print(f"  candidates: {len(state.subjects)}")
    print(f"  sources: {len(state.sources)}")
    print(f"  relationships: {len(state.subject_relationships)}")
    print(f"  expected assessments: {len(state.subjects) * 4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
