from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from investigator.graph import CaseGraph, GraphNode, GraphNodeType
from investigator.roles.focus import InvestigationFocus
from investigator.sources import SourceRecord, SourceRegistry

ROOT = Path(__file__).parent / "fixtures" / "business_law_01"
PUBLIC = ROOT / "public"
HIDDEN = ROOT / "hidden"
CASE_ID = "business_law_01"


@dataclass(frozen=True)
class PublicEvidence:
    source_id: str
    filename: str
    content: str


@dataclass(frozen=True)
class Stage2Fixture:
    case_id: str
    introduction: str
    evidence: tuple[PublicEvidence, ...]
    source_registry: SourceRegistry
    graph: CaseGraph
    focus: InvestigationFocus


def fresh_fixtures() -> list[Stage2Fixture]:
    files = sorted(PUBLIC.glob("*.md"))
    evidence = tuple(PublicEvidence(f"S{index}", path.name, path.read_text(encoding="utf-8")) for index, path in enumerate(files, 1))
    registry = SourceRegistry.from_records(tuple(SourceRecord(item.source_id, item.filename, item.content) for item in evidence))
    nodes = {"U1": GraphNode(id="U1", node_type=GraphNodeType.UNCERTAINTY, statement="What best explains the credible concerns arising from Candidate A's assessment record and conduct?")}
    graph = CaseGraph(case_id=CASE_ID, nodes=nodes)
    return [Stage2Fixture(CASE_ID, "Candidate A's Tutorial 5 assessment has been referred for academic-integrity review after a marker noted sharply uneven performance across related questions and an invigilator recorded repeated adjustments of the candidate's glasses during the assessment.\n\nThe complete available case-source bundle is provided below.\n\nDetermine how the evidence should be organised, what explanations remain plausible, what uncertainty matters, and whether any useful investigative work remains.", evidence, registry, graph, InvestigationFocus(node_id="U1"))]


def run_fixture(fixture: Stage2Fixture) -> Stage2Fixture:
    """Return a fresh immutable fixture instance for one trajectory."""
    if fixture.case_id != CASE_ID:
        raise ValueError(f"Unknown fixture ID: {fixture.case_id!r}")
    return fresh_fixtures()[0]
