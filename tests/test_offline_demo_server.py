"""The offline demo server must answer assessments in the current reply shape and keep Help off real providers."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from investigator import http_api
from investigator.http_api import create_case
from investigator.models import AssessmentSubject
from investigator.vnext import VNextRunInput
from investigator.vnext.model import build_prompt
from investigator.vnext.presets import academic_integrity_core_preset
from investigator.vnext.semantic import InvestigatorSemanticAssessment

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


def test_demo_server_wires_help_to_the_demo_client_even_when_a_provider_key_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    monkeypatch.setenv("ANTHROPIC_MODEL_ID", "not-a-real-model")
    spec = importlib.util.spec_from_file_location("offline_demo_server", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    handler = http_api.InvestigatorApiHandler
    monkeypatch.setattr(handler, "workflow", None, raising=False)
    monkeypatch.setattr(handler, "workspace_agent", None, raising=False)
    real_create_server = http_api.create_server

    def create_server_that_returns_at_once(*args, **kwargs):
        server = real_create_server(*args, **kwargs)
        server.serve_forever = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
        return server

    monkeypatch.setattr(http_api, "create_server", create_server_that_returns_at_once)
    repository = tmp_path / "cases"
    monkeypatch.setattr("sys.argv", ["offline_demo_server.py", "--repository", str(repository), "--port", "0"])
    module.main()
    agent = handler.workspace_agent
    assert isinstance(agent.client, module.OfflineDemoModelClient)
    case_id = create_case(handler.workflow, {"title": "Demo"})["caseId"]
    reply = agent.chat(case_id, "What remains uncertain?")
    assert "controlled Workspace Help provider" in reply.response
    kinds = [call["kind"] for call in json.loads((repository / ".stage7-provider-calls.json").read_text())]
    assert kinds == ["native"]
