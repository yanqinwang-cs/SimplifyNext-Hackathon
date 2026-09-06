"""Deterministic developer/test fixture loaders.

These helpers deliberately live outside the product execution path.  They
materialize only the agent-visible portion of controlled fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path

from investigator.models import AssessmentContext, AssessmentSubject, Source, SourceType, SubjectRelationship
from investigator.state import CaseState


def load_scale_fixture_case_state(fixture_root: str | Path) -> CaseState:
    """Load the visible ten-candidate scale fixture into ordinary case state."""
    root = Path(fixture_root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    source_root = root / "sources"
    filenames = sorted(path.name for path in source_root.glob("*.md"))
    source_ids = {filename: f"S{index:03d}" for index, filename in enumerate(filenames, start=1)}
    shared = set(manifest["shared_files"])
    relationship_by_file = {
        item["source_file"]: item["relationship_id"] for item in manifest.get("relationships", [])
    }
    sources: dict[str, Source] = {}
    for filename in filenames:
        path = source_root / filename
        stem = path.stem.replace("_", " ")
        if filename in shared:
            name = stem.title()
            scope = {
                "scope_type": "relationship" if filename in relationship_by_file else "case",
                "subject_id": None,
                "relationship_id": relationship_by_file.get(filename),
            }
        else:
            letter = filename.split("_")[1]
            name = f"Candidate {letter} {filename.split('_', 2)[2].removesuffix('.md').replace('_', ' ')}"
            scope = {"scope_type": "subject", "subject_id": f"subject_{letter}", "relationship_id": None}
        sources[source_ids[filename]] = Source(
            id=source_ids[filename],
            name=name,
            source_type=SourceType.DOCUMENT,
            content=path.read_text(encoding="utf-8"),
            metadata={"filename": filename, "assessment_scope": scope},
        )
    subjects = {
        f"subject_{letter}": AssessmentSubject(
            subject_id=f"subject_{letter}",
            display_name=f"Candidate {letter}",
            candidate_number=details["candidate_number"],
        )
        for letter, details in manifest["candidates"].items()
    }
    relationships = {
        item["relationship_id"]: SubjectRelationship(
            relationship_id=item["relationship_id"],
            subject_ids=item["subject_ids"],
            relationship_type=item["relationship_type"],
            source_ids=[source_ids[item["source_file"]]],
            description=f"{item['subject_ids'][0]} and {item['subject_ids'][1]} are adjacent.",
        )
        for item in manifest["relationships"]
    }
    return CaseState(
        case_id=manifest["case_id"],
        title=manifest["title"],
        description="Controlled ten-candidate validation fixture.",
        assessment_rule_preset_id=manifest["assessment_rule_preset_id"],
        assessment_context=AssessmentContext(
            assessment_id="BL-ICA2-2026-SCALE",
            title="Business Law Ten Candidate Scale",
            assessment_type="closed-notes individual assessment",
            venue="Seminar Room 4",
        ),
        subjects=subjects,
        subject_relationships=relationships,
        sources=sources,
    )
