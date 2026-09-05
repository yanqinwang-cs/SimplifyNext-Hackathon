import copy
import json
import time
import threading
import urllib.request
from pathlib import Path

import pytest

from investigator.reporting import build_report_record
from investigator.public_views import public_assessment_source_handle
from investigator.http_api import create_server
from investigator.services.vnext_runner import VNextProductionRunner

from test_vnext_product import SequenceClient, assessment, start_vnext


def test_successful_report_is_snapshot_based_and_historical_source_is_exact(tmp_path: Path) -> None:
    workflow = start_vnext(tmp_path, SequenceClient([assessment()]))
    initial = workflow.get_report("case-01")
    assert initial["reportState"] == "available"
    assert initial["assessment"]["students"][0]["displayName"] == "Student"
    run = workflow.get_runs("case-01")[0]
    record = json.loads((tmp_path / "cases/case-01/runs/run_000001/report_record.json").read_text())
    source_handle = public_assessment_source_handle("case-01", record["run_instance_id"], "S1")
    historical = workflow.get_historical_source("case-01", "run_000001", source_handle)
    assert historical["source"]["content"] == "A source record"

    state = workflow.repository.load("case-01")
    state.title = "Renamed now"
    state.sources["S2"] = copy.deepcopy(state.sources["S1"])
    state.sources["S2"].id = "S2"
    state.sources["S2"].name = "new-record.md"
    state.sources["S2"].content = "A later record"
    state.revision += 1
    workflow.repository.save(state)
    current = workflow.get_report("case-01")
    assert current["currentCaseName"] == "Renamed now"
    assert current["assessment"]["caseNameAtAssessment"] == "Business Law Tutorial 5"
    assert [item["displayName"] for item in current["assessment"]["students"]] == ["Student"]
    assert current["assessmentIsStale"] is True
    assert workflow.get_historical_source("case-01", run["run_id"], source_handle)["source"]["content"] == "A source record"


def test_pinned_successful_report_is_not_replaced_by_later_success(tmp_path: Path) -> None:
    workflow = start_vnext(tmp_path, SequenceClient([assessment(), assessment()]))
    workflow.start_run("case-01")
    for _ in range(100):
        if workflow.get_runs("case-01")[-1].get("vnext_status") == "completed":
            break
        time.sleep(0.01)
    first = workflow.get_report("case-01", "run_000001")
    latest = workflow.get_report("case-01")
    assert first["isLatestSuccessfulAssessment"] is False
    assert latest["isLatestSuccessfulAssessment"] is True
    assert first["assessment"]["runHandle"] != latest["assessment"]["runHandle"]


def test_legacy_success_without_report_record_is_honest(tmp_path: Path) -> None:
    workflow = start_vnext(tmp_path, SequenceClient([assessment()]))
    path = tmp_path / "cases/case-01/runs/run_000001/report_record.json"
    path.unlink()
    report = workflow.get_report("case-01")
    assert report["reportState"] == "historical_unavailable"
    assert "Run a new assessment" in report["message"]


def test_report_integrity_rejects_missing_student_coverage(tmp_path: Path) -> None:
    workflow = start_vnext(tmp_path, SequenceClient([assessment()]))
    snapshot_path = tmp_path / "cases/case-01/runs/run_000001/assessment_input_snapshot.json"
    snapshot = json.loads(snapshot_path.read_text())
    result = json.loads((tmp_path / "cases/case-01/runs/run_000001/vnext_result.json").read_text())["result"]
    result["subject_assessments"] = []
    from investigator.vnext.runner import VNextRunResult
    with pytest.raises(ValueError, match="student coverage"):
        build_report_record(snapshot, VNextRunResult.model_validate(result), completed_at="now")


def test_historical_report_and_source_api_are_read_only(tmp_path: Path) -> None:
    workflow = start_vnext(tmp_path, SequenceClient([assessment()]))
    report = workflow.get_report("case-01")
    record = json.loads((tmp_path / "cases/case-01/runs/run_000001/report_record.json").read_text())
    source_handle = public_assessment_source_handle("case-01", record["run_instance_id"], "S1")
    server = create_server(tmp_path / "cases", host="127.0.0.1", port=0, run_callback=lambda *_: None, run_mode="vnext")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        pinned = urllib.request.urlopen(f"{base}/api/cases/case-01/assessment-runs/{report['assessment']['runHandle']}/report").read()
        source = urllib.request.urlopen(f"{base}/api/cases/case-01/assessment-runs/{report['assessment']['runHandle']}/sources/{source_handle}").read()
        assert json.loads(pinned)["assessment"]["students"][0]["displayName"] == "Student"
        assert json.loads(source)["source"]["content"] == "A source record"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
