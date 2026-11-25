"""The offline demo server must answer assessments in the current reply shape and keep Help off real providers."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from investigator.http_api import create_case
from investigator.models import AssessmentSubject
from investigator.services.evidence_requests import HumanEvidenceWorkflow
from investigator.state.repository import CaseRepository
from investigator.vnext import VNextRunInput
from investigator.vnext.model import build_prompt
from investigator.vnext.presets import academic_integrity_core_preset
from investigator.vnext.semantic import InvestigatorSemanticAssessment
from investigator.workspace_agent import WorkspaceAgent

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "offline_demo_server.py"


@pytest.fixture
def demo_client(tmp_path: Path):
    spec = importlib.util.spec_from_file_location("offline_demo_server", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.OfflineDemoModelClient(log_path=tmp_path / "calls.json")


def test_demo_assessment_reply_parses_in_the_current_semantic_shape(demo_client) -> None:
    run_input = VNextRunInput(
        case_id="c",
        sources={},
        subjects={"subject_1": AssessmentSubject(subject_id="subject_1", display_name="Student 1")},
        rule_preset=academic_integrity_core_preset(),
    )
    result = demo_client.call(build_prompt(run_input), InvestigatorSemanticAssessment)
    assert [item.subject_id for item in result.parsed.subject_assessments] == ["subject_1"]


def test_help_uses_the_demo_client_even_when_a_provider_key_is_set(
    demo_client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    monkeypatch.setenv("ANTHROPIC_MODEL_ID", "not-a-real-model")
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"))
    case_id = create_case(workflow, {"title": "Demo"})["caseId"]
    reply = WorkspaceAgent(workflow, client=demo_client).chat(case_id, "What remains uncertain?")
    assert "controlled Workspace Help provider" in reply.response
    kinds = [call["kind"] for call in json.loads((tmp_path / "calls.json").read_text())]
    assert kinds == ["native"]
