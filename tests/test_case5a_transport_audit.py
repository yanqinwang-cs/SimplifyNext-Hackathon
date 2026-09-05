import json
import sys
import threading
import time
import types
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from botocore.exceptions import ReadTimeoutError

from investigator.http_api import create_case, create_server
from investigator.llm import (
    BEDROCK_CONNECT_TIMEOUT_SECONDS,
    BEDROCK_READ_TIMEOUT_SECONDS,
    BedrockModelClient,
    CredentialOverride,
    ModelCallResult,
    bedrock_transport_config,
    clear_credential_override,
    set_credential_override,
)
from investigator.public_views import public_run_handle_for_instance
from investigator.services.evidence_requests import HumanEvidenceWorkflow
from investigator.services.vnext_runner import VNextProductionRunner
from investigator.state import CaseRepository
from investigator.models.source import Source, SourceType


class TimeoutClient:
    def __init__(self) -> None:
        self.calls = 0

    def call(self, _prompt: object, _schema: type[object]) -> ModelCallResult:
        self.calls += 1
        raise ReadTimeoutError(
            endpoint_url="https://bedrock-runtime.us-east-1.amazonaws.com/model/fake/converse",
            error="PRE5A_AUDIT_ACCESS PRE5A_AUDIT_SECRET PRE5A_AUDIT_SESSION",
        )


def add_source(workflow: HumanEvidenceWorkflow, case_id: str = "case-01", content: str = "A controlled record.") -> None:
    state = workflow.ensure_case(case_id)
    state.sources["S1"] = Source(id="S1", name="record.txt", source_type=SourceType.DOCUMENT, content=content)
    workflow.repository.save(state)


def wait_for(workflow: HumanEvidenceWorkflow, case_id: str = "case-01") -> None:
    for _ in range(200):
        if workflow.get_workspace(case_id)["runtimeStatus"] in {"COMPLETED", "FAILED"}:
            return
        time.sleep(0.01)
    raise AssertionError("assessment did not reach a terminal state")


def start_timeout_workflow(tmp_path: Path) -> tuple[HumanEvidenceWorkflow, TimeoutClient]:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode="vnext")
    workflow.ensure_case("case-01")
    add_source(workflow)
    client = TimeoutClient()
    workflow.run_callback = VNextProductionRunner(client).run
    workflow.start_run("case-01")
    wait_for(workflow)
    return workflow, client


def get(url: str) -> tuple[int, dict]:
    try:
        with urlopen(url) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_bedrock_transport_config_is_explicit_and_bounded() -> None:
    config = bedrock_transport_config()
    assert config.connect_timeout == BEDROCK_CONNECT_TIMEOUT_SECONDS == 10
    assert config.read_timeout == BEDROCK_READ_TIMEOUT_SECONDS == 300
    assert config.retries == {"mode": "standard", "max_attempts": 1}


def test_default_and_temporary_credential_paths_use_the_same_config(monkeypatch) -> None:
    calls: list[tuple[str, dict]] = []

    class FakeSession:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            calls.append(("session_init", kwargs))

        def client(self, service, **kwargs):
            calls.append(("session", {"service": service, **kwargs}))
            return object()

    fake_boto3 = types.SimpleNamespace(
        client=lambda service, **kwargs: calls.append(("default", {"service": service, **kwargs})) or object(),
        Session=FakeSession,
    )
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    clear_credential_override()
    default = BedrockModelClient(model_id="model-a", region="us-east-1")
    default._client_for_call()
    set_credential_override(CredentialOverride("ACCESS", "SECRET", "SESSION", "us-west-2"))
    temporary = BedrockModelClient(model_id="model-b", region="us-east-1")
    temporary._client_for_call()
    clear_credential_override()

    assert calls[0][0] == "default"
    assert calls[0][1]["service"] == "bedrock-runtime"
    assert calls[0][1]["region_name"] == "us-east-1"
    assert calls[1][0] == "session_init"
    assert calls[1][1]["region_name"] == "us-west-2"
    assert calls[2][0] == "session"
    assert calls[2][1]["service"] == "bedrock-runtime"
    assert calls[0][1]["config"].connect_timeout == calls[2][1]["config"].connect_timeout == 10
    assert calls[0][1]["config"].read_timeout == calls[2][1]["config"].read_timeout == 300
    assert calls[0][1]["config"].retries == calls[2][1]["config"].retries == {"mode": "standard", "max_attempts": 1}


def test_injected_client_does_not_construct_boto3(monkeypatch) -> None:
    def fail(*_args, **_kwargs):
        raise AssertionError("boto3 must not be constructed for an injected client")

    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=fail, Session=fail))
    injected = object()
    client = BedrockModelClient(model_id="model", client=injected)
    assert client._client_for_call() is injected


def test_read_timeout_is_terminal_without_clean_retry_or_report(tmp_path: Path) -> None:
    workflow, client = start_timeout_workflow(tmp_path)
    run = workflow.get_runs("case-01")[0]
    artifact = tmp_path / "cases" / "case-01" / "runs" / run["run_id"]
    traces = workflow.get_traces("case-01")
    failed = next(item for item in traces if item.get("event") == "vnext_attempt_failed")

    assert client.calls == 1
    assert run["model_calls"] == 1
    assert run["clean_execution_retries"] == 0
    assert run["proposal_correction_calls"] == 0
    assert workflow.get_runs("case-01")[0]["outcome_type"] == "FAILED"
    assert not (artifact / "vnext_result.json").exists()
    assert not (artifact / "report_record.json").exists()
    assert failed["failure_category"] == "PROVIDER_TIMEOUT"
    assert failed["technical_error_type"] == "ReadTimeoutError"
    assert "PRE5A_AUDIT_" not in json.dumps(traces)


def test_audit_trace_is_handle_bound_sanitized_and_gated(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "PRE5A_AUDIT_ACCESS")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "PRE5A_AUDIT_SECRET")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "PRE5A_AUDIT_SESSION")
    workflow, client = start_timeout_workflow(tmp_path)
    run = workflow.get_runs("case-01")[0]
    handle = public_run_handle_for_instance("case-01", run["run_id"], run["run_instance_id"])
    assert client.calls == 1

    server = create_server(tmp_path / "api-cases", port=0, run_callback=lambda *_: None, run_mode="vnext")
    server.RequestHandlerClass.workflow = workflow
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/api/cases/case-01/assessment-runs/{handle}/audit-trace"
    try:
        monkeypatch.delenv("SIMPLIFYNEXT_ENABLE_DIAGNOSTIC_API", raising=False)
        assert get(url)[0] == 404
        monkeypatch.setenv("SIMPLIFYNEXT_ENABLE_DIAGNOSTIC_API", "1")
        status, payload = get(url)
        assert status == 200
        assert payload["caseId"] == "case-01"
        assert payload["runHandle"] == handle
        assert payload["outcome"] == "failed"
        assert payload["model"]["logicalModel"] == "anthropic.claude-sonnet-4-5"
        assert payload["counters"] == {"modelCalls": 1, "proposalCorrectionCalls": 0, "cleanExecutionRetries": 0}
        assert payload["failure"]["category"] == "PROVIDER_TIMEOUT"
        assert payload["failure"]["technicalType"] == "ReadTimeoutError"
        assert [item["event"] for item in payload["trace"]] == [item["event"] for item in workflow.get_traces("case-01") if item.get("event") in {"vnext_attempt_started", "vnext_attempt_failed", "failed"}]
        encoded = json.dumps(payload)
        assert "PRE5A_AUDIT_" not in encoded
        assert run["run_id"] not in encoded
        assert get(f"http://127.0.0.1:{server.server_address[1]}/api/cases/case-02/assessment-runs/{handle}/audit-trace")[0] == 404
        assert get(f"http://127.0.0.1:{server.server_address[1]}/api/cases/case-01/assessment-runs/not-a-real-handle/audit-trace")[0] == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_successful_run_also_has_an_operator_audit_trace(tmp_path: Path) -> None:
    workflow = HumanEvidenceWorkflow(CaseRepository(tmp_path / "cases"), run_mode="vnext")
    create_case(workflow, {"title": "No evidence"})
    workflow.run_callback = VNextProductionRunner().run
    workflow.start_run("case-000001")
    wait_for(workflow, "case-000001")
    run = workflow.get_runs("case-000001")[0]
    handle = public_run_handle_for_instance("case-000001", run["run_id"], run["run_instance_id"])
    audit = workflow.audit_trace("case-000001", run["run_id"], handle)
    assert audit["outcome"] == "completed"
    assert audit["counters"]["modelCalls"] == 0
    assert audit["failure"] is None
    assert any(item.get("event") == "vnext_completed" for item in audit["trace"])
