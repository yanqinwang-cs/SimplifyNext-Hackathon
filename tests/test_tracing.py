import json
from pathlib import Path

from investigator.tracing import InteractiveTrace, InteractiveTraceWriter, new_session_id, utc_now


def make_trace() -> InteractiveTrace:
    now = utc_now()
    return InteractiveTrace(
        session_id=new_session_id(), case_id="case_01", environment_id="case_01_controlled",
        model_id="fixture", started_at=now, updated_at=now, status="starting", initial_prompt="prompt",
    )


def test_trace_writer_creates_one_run_and_preserves_events(tmp_path: Path) -> None:
    writer = InteractiveTraceWriter(tmp_path / "runs", make_trace())
    assert writer.trace_path.parent.name == writer.trace.session_id
    writer.record("investigation_started", {"case_id": "case_01"})
    writer.update(status="awaiting_action_review", current_case_state={"revision": 0})
    writer.record("action_proposed", {"action_id": "A1"})

    saved = json.loads(writer.trace_path.read_text(encoding="utf-8"))
    assert saved["status"] == "awaiting_action_review"
    assert [event["type"] for event in saved["events"]] == ["investigation_started", "action_proposed"]
    assert saved["current_case_state"] == {"revision": 0}
    assert not list(writer.trace_path.parent.glob("*.tmp"))


def test_trace_writer_preserves_raw_failed_output(tmp_path: Path) -> None:
    writer = InteractiveTraceWriter(tmp_path / "runs", make_trace())
    raw = '{"invalid": true}'
    writer.update(latest_error={"stage": "initial_parse", "message": "invalid", "raw_model_output": raw})
    writer.record("model_error", {"stage": "initial_parse", "raw_model_output": raw})
    saved = json.loads(writer.trace_path.read_text(encoding="utf-8"))
    assert saved["latest_error"]["raw_model_output"] == raw
    assert saved["events"][0]["payload"]["raw_model_output"] == raw
