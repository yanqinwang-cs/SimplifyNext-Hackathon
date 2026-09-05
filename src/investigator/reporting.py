"""Immutable vNext assessment snapshots and deterministic report projection."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import re
from typing import Any

from investigator.graph import CaseGraph
from investigator.models.assessment import AssessmentSubject
from investigator.models.source import Source
from investigator.public_views import document_format, public_assessment_source_handle
from investigator.vnext.models import AssessmentRulePreset, AssessmentStatus
from investigator.vnext.source_applicability import build_source_applicability, source_applicability_snapshot
from investigator.vnext.provenance import source_ancestry


REPORT_SCHEMA_VERSION = 1


def source_snapshot(source: Source) -> dict[str, Any]:
    text = source.content or ""
    return {
        "source_id": source.id,
        "filename": source.name,
        "document_format": document_format(source.name),
        "content": text,
        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def build_input_snapshot(state: Any, preset: AssessmentRulePreset | Mapping[str, Any], run_instance_id: str) -> dict[str, Any]:
    """Copy the exact admitted semantic inputs into a private run artifact."""

    students = list(state.subjects.values())
    admitted_students = students or [AssessmentSubject(subject_id="case_subject", display_name="Student")]
    admitted_sources = {key: value for key, value in state.sources.items() if (value.content or "").strip()}
    applicability = build_source_applicability(admitted_sources, state.subjects, state.subject_relationships)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_instance_id": run_instance_id,
        "case_id": state.case_id,
        "case_name_at_assessment": state.title,
        "assessment_input_revision": state.revision,
        "preset": preset.model_dump(mode="json") if isinstance(preset, AssessmentRulePreset) else dict(preset),
        "students": [
            {
                "subject_id": student.subject_id,
                "display_name": student.display_name,
                "candidate_number": student.candidate_number,
            }
            for student in admitted_students
        ],
        "sources": [source_snapshot(source) for source in admitted_sources.values()],
        "relationships": [relationship.model_dump(mode="json") for relationship in state.subject_relationships.values()],
        "source_applicability": source_applicability_snapshot(applicability),
    }


def _humanize(text: str, snapshot: Mapping[str, Any]) -> str:
    replacements: list[tuple[str, str]] = []
    for student in snapshot.get("students", []):
        replacements.append((str(student["subject_id"]), str(student["display_name"])))
    for source in snapshot.get("sources", []):
        replacements.append((str(source["source_id"]), "the source"))
    for relationship in snapshot.get("relationships", []):
        replacements.append((str(relationship["relationship_id"]), "the relationship"))
    replacements.sort(key=lambda item: len(item[0]), reverse=True)
    result = str(text or "")
    for internal, replacement in replacements:
        result = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(internal)}(?![A-Za-z0-9_])", replacement, result)
    result = re.sub(r"(?<![A-Za-z0-9_])rel_[a-f0-9]{12,64}(?![A-Za-z0-9_])", "the relationship", result)
    return result


def _material(
    graph: CaseGraph,
    node_id: str,
    snapshot: Mapping[str, Any],
    run_instance_id: str,
) -> dict[str, Any]:
    if node_id not in graph.nodes:
        raise ValueError(f"Report references missing graph node {node_id!r}")
    source_by_id = {str(item["source_id"]): item for item in snapshot.get("sources", [])}
    source_ids = source_ancestry(graph, node_id)
    unknown = sorted(source_ids - set(source_by_id))
    if unknown:
        raise ValueError(f"Report references sources outside the admitted snapshot: {unknown}")
    sources = [
        {
            "source_id": item["source_id"],
            "source_handle": public_assessment_source_handle(snapshot["case_id"], run_instance_id, item["source_id"]),
            "file_name": item["filename"],
        }
        for item in snapshot.get("sources", [])
        if item["source_id"] in source_ids
    ]
    node = graph.nodes[node_id]
    return {"statement": _humanize(node.statement, snapshot), "sources": sources}


def build_report_record(
    snapshot: Mapping[str, Any],
    result: Any,
    *,
    completed_at: str,
) -> dict[str, Any]:
    """Validate coverage and construct the private immutable report artifact."""

    graph = result.graph if isinstance(result.graph, CaseGraph) else CaseGraph.model_validate(result.graph)
    students = list(snapshot.get("students", []))
    violations = list(snapshot.get("preset", {}).get("violations", []))
    expected_students = [str(item["subject_id"]) for item in students]
    expected_violations = [str(item["violation_id"]) for item in violations]
    assessments = list(result.subject_assessments)
    actual_students = [item.subject_id for item in assessments]
    if actual_students != expected_students or len(actual_students) != len(set(actual_students)):
        raise ValueError("Report integrity failure: assessed student coverage does not match the admitted snapshot")
    label_by_violation = {item["violation_id"]: item["label"] for item in violations}
    student_sections: list[dict[str, Any]] = []
    for assessment in assessments:
        findings = list(assessment.violation_assessments)
        actual_violations = [item.violation_id for item in findings]
        if actual_violations != expected_violations or len(actual_violations) != len(set(actual_violations)):
            raise ValueError(f"Report integrity failure: violation coverage for {assessment.subject_id!r} is invalid")
        student_sections.append({
            "subject_id": assessment.subject_id,
            "display_name": next(item["display_name"] for item in students if item["subject_id"] == assessment.subject_id),
            "candidate_number": next(item.get("candidate_number") for item in students if item["subject_id"] == assessment.subject_id),
            "violations": [
                {
                    "violation_id": item.violation_id,
                    "label": label_by_violation[item.violation_id],
                    "status": AssessmentStatus(item.status).value,
                    "reasoning_summary": _humanize(item.reasoning_summary, snapshot),
                    "supporting_material": [_material(graph, node_id, snapshot, snapshot["run_instance_id"]) for node_id in item.supporting_node_ids],
                    "limiting_material": [_material(graph, node_id, snapshot, snapshot["run_instance_id"]) for node_id in item.mitigating_node_ids],
                    "unresolved_points": [_humanize(point, snapshot) for point in item.unresolved_points],
                }
                for item in findings
            ],
            "alternative_explanations": [
                {
                    "statement": _humanize(item.statement, snapshot),
                    "supporting_material": [
                        _material(graph, node_id, snapshot, snapshot["run_instance_id"])
                        for node_id in item.source_node_ids
                    ],
                }
                for item in getattr(assessment, "alternative_explanations", [])
            ],
            "furthest_conclusion": _humanize(assessment.furthest_conclusion.statement, snapshot),
        })
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_instance_id": snapshot["run_instance_id"],
        "case_id": snapshot["case_id"],
        "case_name_at_assessment": snapshot["case_name_at_assessment"],
        "assessment_input_revision": snapshot["assessment_input_revision"],
        "completed_at": completed_at,
        "preset": snapshot.get("preset", {}),
        "students": student_sections,
        "sources": snapshot.get("sources", []),
        "source_applicability": snapshot.get("source_applicability", {}),
        "relationships": snapshot.get("relationships", []),
        "validated_source_applicability": getattr(result.metadata, "source_applicability", {}),
        "relationship_scope_ids": getattr(result.metadata, "relationship_scope_ids", {}),
    }


def public_report_from_record(
    record: Mapping[str, Any],
    *,
    current_case_name: str,
    report_state: str,
    is_latest_successful_assessment: bool,
    run_handle: str,
) -> dict[str, Any]:
    run_instance_id = str(record["run_instance_id"])
    case_id = str(record["case_id"])
    return {
        "caseId": case_id,
        "currentCaseName": current_case_name,
        "reportState": "available",
        "assessmentIsStale": report_state == "stale",
        "isLatestSuccessfulAssessment": is_latest_successful_assessment,
        "assessment": {
            "runHandle": run_handle,
            "caseNameAtAssessment": record["case_name_at_assessment"],
            "completedAt": record["completed_at"],
            "students": [
                {
                    "sectionHandle": public_assessment_source_handle(case_id, run_instance_id, f"student:{student['subject_id']}"),
                    "displayName": student["display_name"],
                    "violations": [
                        {
                            "label": finding["label"],
                            "status": finding["status"],
                            "reasoningSummary": finding["reasoning_summary"],
                            "supportingMaterial": [
                                {
                                    "statement": material["statement"],
                                    "sources": [
                                        {"sourceHandle": source["source_handle"], "fileName": source["file_name"]}
                                        for source in material["sources"]
                                    ],
                                }
                                for material in finding["supporting_material"]
                            ],
                            "limitingMaterial": [
                                {
                                    "statement": material["statement"],
                                    "sources": [
                                        {"sourceHandle": source["source_handle"], "fileName": source["file_name"]}
                                        for source in material["sources"]
                                    ],
                                }
                                for material in finding["limiting_material"]
                            ],
                            "unresolvedPoints": finding["unresolved_points"],
                        }
                        for finding in student["violations"]
                    ],
                    "furthestConclusion": student["furthest_conclusion"],
                    "alternativeExplanations": [
                        {
                            "statement": item["statement"],
                            "supportingMaterial": [
                                {
                                    "statement": material["statement"],
                                    "sources": [
                                        {"sourceHandle": source["source_handle"], "fileName": source["file_name"]}
                                        for source in material["sources"]
                                    ],
                                }
                                for material in item["supporting_material"]
                            ],
                        }
                        for item in student.get("alternative_explanations", [])
                    ],
                }
                for student in record["students"]
            ],
        },
    }
