"""Regression tests for the model-facing review bugs: prompt, semantic checks, provider adapter, Help and reports."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from investigator.graph import CaseGraph, GraphNode, GraphNodeType
from investigator.llm import ModelCallMetadata, ModelNativeCall, ModelParseError, ModelTextBlock
from investigator.llm.anthropic import ANTHROPIC_CONNECT_TIMEOUT_SECONDS, AnthropicModelClient
from investigator.llm.base import ModelToolUse
from investigator.models import AssessmentContext, AssessmentSubject, Source, SourceType
from investigator.reporting import _humanize
from investigator.runtime_settings import APPROVED_MODELS, MODEL_REGISTRY, _latest_investigator_use
from investigator.services.evidence_requests import HumanEvidenceWorkflow
from investigator.services.vnext_runner import VNextProductionRunner
from investigator.state import CaseRepository, CaseState
from investigator.vnext import AssessmentRulePreset, AssessmentStatus, Confidence, ViolationDefinition, VNextRunInput
from investigator.vnext.model import VNextInvestigatorModel, build_prompt
from investigator.vnext.provenance import source_ancestry
from investigator.vnext.semantic import (
    InvestigatorSemanticAssessment,
    SemanticItem,
    SemanticItemKind,
    SemanticSubjectAssessment,
    SemanticValidationError,
    SemanticViolationAssessment,
    compile_semantic_assessment,
)
from investigator.workspace_agent import WorkspaceAgent

PRESET = AssessmentRulePreset(
    preset_id="p",
    violations=[ViolationDefinition(violation_id="V1", label="Rule", rule_text="Rule", prohibited_conduct="Conduct")],
)
SUBJECTS = {
    "subject_A": AssessmentSubject(subject_id="subject_A", display_name="Candidate A"),
    "subject_B": AssessmentSubject(subject_id="subject_B", display_name="Candidate B"),
}


def _run_input(sources: dict[str, Source], relationships=None) -> VNextRunInput:
    return VNextRunInput(
        case_id="c",
        assessment_context=AssessmentContext(assessment_id="a"),
        subjects=SUBJECTS,
        subject_relationships=relationships or {},
        sources=sources,
        rule_preset=PRESET,
    )


def _source(source_id: str, content: str) -> Source:
    return Source(id=source_id, name=f"{source_id}.md", source_type=SourceType.DOCUMENT, content=content)


# --- high: a source naming two students must reach the model ---------------------------------------------------


def test_source_naming_two_students_reaches_the_prompt_as_uncitable_context() -> None:
    run_input = _run_input({"S1": _source("S1", "Candidate A passed a note to Candidate B. MARKER-XYZ")})
    assert run_input.source_applicability["S1"].permitted_subject_ids == []
    prompt = build_prompt(run_input)
    assert "MARKER-XYZ" in prompt
    assert '"student_specific": {}' in prompt


# --- semantic validation --------------------------------------------------------------------------------------


def _assessment(items: list[SemanticItem], support: list[str]) -> InvestigatorSemanticAssessment:
    def subject(subject_id: str, status: AssessmentStatus, refs: list[str]) -> SemanticSubjectAssessment:
        return SemanticSubjectAssessment(
            subject_id=subject_id,
            violation_assessments=[
                SemanticViolationAssessment(
                    violation_id="V1",
                    status=status,
                    supporting_item_refs=refs,
                    reasoning_summary="Bounded.",
                    confidence=Confidence.LOW,
                )
            ],
            furthest_conclusion={"statement": "Bounded.", "confidence": "low"},
        )

    return InvestigatorSemanticAssessment(
        semantic_items=items,
        subject_assessments=[
            subject("subject_A", AssessmentStatus.SUPPORTED, support),
            subject("subject_B", AssessmentStatus.NOT_CURRENTLY_SUPPORTED, []),
        ],
    )


def _evidence(source_ids: list[str]) -> SemanticItem:
    return SemanticItem(
        local_ref="e1",
        kind=SemanticItemKind.EVIDENCE_STATEMENT,
        statement="A record says X.",
        basis_source_ids=source_ids,
    )


def test_support_from_a_source_naming_no_student_is_a_retryable_semantic_error() -> None:
    run_input = _run_input({"S1": _source("S1", "The room was warm.")})
    with pytest.raises(SemanticValidationError) as raised:
        compile_semantic_assessment(_assessment([_evidence(["S1"])], ["e1"]), run_input)
    assert raised.value.retry_constraint


def test_unknown_source_id_is_a_retryable_semantic_error_not_a_key_error() -> None:
    run_input = _run_input({"S1": _source("S1", "Candidate A wrote notes.")})
    with pytest.raises(SemanticValidationError) as raised:
        compile_semantic_assessment(_assessment([_evidence(["S99"])], ["e1"]), run_input)
    assert "S99" in str(raised.value) and raised.value.retry_constraint


def test_repeated_source_id_is_collapsed_instead_of_failing() -> None:
    run_input = _run_input({"S1": _source("S1", "Candidate A wrote notes.")})
    compiled = compile_semantic_assessment(_assessment([_evidence(["S1", "S1"])], ["e1"]), run_input)
    evidence = [u for u in compiled.proposal.graph_updates if getattr(u, "local_ref", "") == "e1"][0]
    assert evidence.source_ids == ["S1"]


# --- provider adapter -----------------------------------------------------------------------------------------


class Answer(BaseModel):
    answer: str


class _Messages:
    def __init__(self, response: SimpleNamespace) -> None:
        self.response = response

    def create(self, **_kwargs):
        return self.response


def _anthropic(stop_reason: str, text: str) -> AnthropicModelClient:
    response = SimpleNamespace(
        model="claude-test",
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=11, output_tokens=7),
        stop_reason=stop_reason,
    )
    return AnthropicModelClient(model_id="claude-test", client=SimpleNamespace(messages=_Messages(response)))


def test_unparseable_reply_keeps_its_token_usage_and_stop_reason() -> None:
    with pytest.raises(ModelParseError) as raised:
        _anthropic("end_turn", "not json").call("x", Answer)
    metadata = raised.value.metadata
    assert (metadata.input_tokens, metadata.output_tokens, metadata.finish_reason) == (11, 7, "end_turn")
    assert metadata.parse_success is False


def test_reply_cut_off_at_the_token_limit_is_not_retried_with_the_same_limit() -> None:
    from investigator.llm import ModelOutputTruncated

    with pytest.raises(ModelOutputTruncated) as raised:
        _anthropic("max_tokens", '{"answer": "unfin').call("x", Answer)
    assert raised.value.metadata.finish_reason == "max_tokens"
    assert VNextProductionRunner._retryable(raised.value) is False
    assert VNextProductionRunner._retryable(ModelParseError("bad")) is True


def test_connect_timeout_is_applied_to_the_real_client(monkeypatch: pytest.MonkeyPatch) -> None:
    import anthropic

    captured: dict = {}
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kwargs: captured.update(kwargs) or object())
    AnthropicModelClient(model_id="m", api_key="k")._client_for_call()
    assert captured["timeout"].connect == ANTHROPIC_CONNECT_TIMEOUT_SECONDS


def test_investigator_model_has_no_stale_old_schema_entry_point() -> None:
    assert not callable(VNextInvestigatorModel(client=SimpleNamespace()))


# --- reports, settings, provenance ---------------------------------------------------------------------------


def test_student_name_with_a_backslash_is_inserted_literally() -> None:
    snapshot = {"students": [{"subject_id": "subject_1", "display_name": r"A\Kim \1"}], "sources": []}
    assert _humanize("subject_1 was seated alone.", snapshot) == r"A\Kim \1 was seated alone."


@pytest.mark.parametrize(
    ("outcome_type", "expected"),
    [("RUNNING", "running"), (None, "running"), ("COMPLETED", "completed"), ("FAILED", "failed")],
)
def test_runtime_settings_labels_the_latest_run_by_its_recorded_outcome(
    outcome_type: str | None, expected: str
) -> None:
    # Production writes outcome_type "RUNNING" when a run starts, so that value must read as running.
    model = APPROVED_MODELS[0]
    run = {"model": MODEL_REGISTRY[model].invocation_id, "started_at": "t", "outcome_type": outcome_type}
    use, _ = _latest_investigator_use("case-1", SimpleNamespace(get_runs=lambda _case: [run]))
    assert use["outcome"] == expected


def test_provenance_reports_the_source_node_reached_not_the_start_node(monkeypatch: pytest.MonkeyPatch) -> None:
    # The graph contract does not allow an edge into a source node today, so the traversal is driven through a stand-in
    # edge: this pins what the walk records when it does reach one.
    graph = CaseGraph(
        case_id="c",
        nodes={
            "P1": GraphNode(id="P1", node_type=GraphNodeType.PROPOSITION, statement="p"),
            "S1": GraphNode(id="S1", node_type=GraphNodeType.SOURCE, statement="s"),
        },
        edges={},
    )
    monkeypatch.setattr(
        CaseGraph,
        "outgoing",
        lambda self, node_id, relation=None: [SimpleNamespace(target_id="S1")] if node_id == "P1" else [],
    )
    assert source_ancestry(graph, "P1") == {"S1"}


# --- Help ---------------------------------------------------------------------------------------------------


def _text(text: str) -> ModelNativeCall:
    return ModelNativeCall(
        text_blocks=[ModelTextBlock(text=text)],
        metadata=ModelCallMetadata(provider="fake", model="m", latency_seconds=0, parse_success=True),
    )


def _workflow(tmp_path) -> tuple[HumanEvidenceWorkflow, str]:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"))
    workflow.repository.save(CaseState(case_id="case-1", title="t"))
    return workflow, "case-1"


def test_interleaved_help_turns_do_not_break_later_turns(tmp_path) -> None:
    workflow, case_id = _workflow(tmp_path)
    gate = threading.Event()

    class Fake:
        def call_native(self, messages, tools):
            last = messages[-1]
            if last["role"] == "user" and last["text"] == "A":
                return ModelNativeCall(
                    tool_uses=[ModelToolUse(call_id="a1", name="LIST_SOURCES", arguments={})],
                    metadata=ModelCallMetadata(provider="f", model="m", latency_seconds=0, parse_success=True),
                )
            return _text(f"answer to {last.get('text') or 'tool'}")

    agent = WorkspaceAgent(workflow, client=Fake())
    original = agent.invoke_tool
    agent.invoke_tool = lambda case, request: (gate.wait(5), original(case, request))[1]
    first = threading.Thread(target=lambda: agent.chat(case_id, "A"))
    first.start()
    time.sleep(0.3)
    second = threading.Thread(target=lambda: agent.chat(case_id, "B"))
    second.start()
    time.sleep(0.2)
    gate.set()
    first.join(5)
    second.join(5)
    assert agent.chat(case_id, "C").response == "answer to C"
    assert agent.chat(case_id, "D").response == "answer to D"


def test_help_does_not_repeat_a_provider_call_that_timed_out(tmp_path) -> None:
    workflow, case_id = _workflow(tmp_path)

    class Slow:
        calls = 0

        def call_native(self, messages, tools):
            Slow.calls += 1
            raise TimeoutError("read timed out")

    result = WorkspaceAgent(workflow, client=Slow()).chat(case_id, "hello")
    assert Slow.calls == 1
    assert "temporarily unavailable" in result.response


def test_help_rejects_a_blank_message_without_calling_the_model(tmp_path) -> None:
    workflow, case_id = _workflow(tmp_path)

    class Never:
        def call_native(self, messages, tools):
            raise AssertionError("a blank message must not reach the provider")

    with pytest.raises(ValueError):
        WorkspaceAgent(workflow, client=Never()).chat(case_id, "   ")


# --- bedrock parse failures, public failure text -----------------------------------------------------------------


def _bedrock(stop_reason: str, text: str):
    from investigator.llm.bedrock import BedrockModelClient

    response = {
        "output": {"message": {"content": [{"text": text}]}},
        "usage": {"inputTokens": 21, "outputTokens": 9},
        "stopReason": stop_reason,
    }
    return BedrockModelClient(model_id="m", client=SimpleNamespace(converse=lambda **_kwargs: response))


def test_bedrock_parse_failure_keeps_usage_and_a_cut_off_reply_is_not_retried() -> None:
    from investigator.llm import ModelOutputTruncated

    with pytest.raises(ModelParseError) as raised:
        _bedrock("end_turn", "not json").call("x", Answer)
    assert (raised.value.metadata.input_tokens, raised.value.metadata.output_tokens) == (21, 9)
    assert not isinstance(raised.value, ModelOutputTruncated)
    with pytest.raises(ModelOutputTruncated) as truncated:
        _bedrock("max_tokens", '{"answer": "unfin').call("x", Answer)
    assert truncated.value.metadata.finish_reason == "max_tokens"
    assert VNextProductionRunner._retryable(truncated.value) is False


def test_public_failure_message_never_exposes_internal_ids() -> None:
    from investigator.public_views import GENERIC_FAILURE_MESSAGE, public_failure_message

    internal = {
        "failure_category": "SemanticValidationError",
        "message": "'subject_1'/V1 is supported only by case-wide or unattributed material",
    }
    assert public_failure_message(internal) == GENERIC_FAILURE_MESSAGE
    assert public_failure_message({"failure_category": "RuntimeError", "message": "raw S3 detail"}) == (
        GENERIC_FAILURE_MESSAGE
    )
    timeout = {"failure_category": "PROVIDER_TIMEOUT", "message": "The model provider did not respond in time."}
    assert public_failure_message(timeout) == "The model provider did not respond in time."
    assert public_failure_message(None) == ""
    restart = {"failure_category": "PROCESS_RESTART", "message": "anything"}
    assert public_failure_message(restart) == "The assessment was interrupted when the service restarted."
    for category in (
        "AnthropicConfigurationError",
        "BedrockConfigurationError",
        "ModelProviderConfigurationError",
        "ORCHESTRATION_CONFIGURATION_FAILURE",
    ):
        message = public_failure_message({"failure_category": category, "message": "set ANTHROPIC_API_KEY sk-secret"})
        assert "sk-secret" not in message
        assert message.startswith("The model provider is not configured")
