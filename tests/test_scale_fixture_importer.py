from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

from investigator.http_api import SAMPLE_CASE_IDS, sample_cases, seed_sample_case
from investigator.reporting import build_input_snapshot, build_report_record
from investigator.services.evidence_requests import HumanEvidenceWorkflow
from investigator.state import CaseRepository
from investigator.test_fixtures import load_scale_fixture_case_state
from investigator.vnext import run_input_from_case_state
from investigator.vnext.model import build_prompt
from investigator.vnext.presets import academic_integrity_core_preset


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/vnext_scale/case_10_5a_plus_5b"
SCRIPT = ROOT / "scripts/load_scale_fixture.py"


def run_import(repository: Path, case_id: str, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(FIXTURE), "--repository", str(repository), "--case-id", case_id, *extra],
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "src")},
        text=True,
        capture_output=True,
        check=False,
    )


def test_importer_loads_ordinary_ten_candidate_case(tmp_path: Path) -> None:
    result = run_import(tmp_path / "cases", "scale-10-working")
    assert result.returncode == 0, result.stderr
    state = CaseRepository(tmp_path / "cases").load("scale-10-working")
    assert state.case_kind == "user"
    assert state.sample_id is None
    assert len(state.subjects) == 10
    assert len(state.sources) == 24
    assert len(state.subject_relationships) == 2
    assert {tuple(item.subject_ids) for item in state.subject_relationships.values()} == {
        ("subject_A", "subject_B"), ("subject_F", "subject_G")
    }
    assert "expected assessments: 40" in result.stdout


def test_importer_rejects_existing_case_and_replace_is_scoped(tmp_path: Path) -> None:
    repository = tmp_path / "cases"
    first = run_import(repository, "scale-10-working")
    assert first.returncode == 0
    other = run_import(repository, "other-case")
    assert other.returncode == 0
    rejected = run_import(repository, "scale-10-working")
    assert rejected.returncode != 0
    replaced = run_import(repository, "scale-10-working", "--replace", "--title", "Replaced")
    assert replaced.returncode == 0
    cases = CaseRepository(repository)
    assert cases.load("scale-10-working").title == "Replaced"
    assert cases.load("other-case").title == load_scale_fixture_case_state(FIXTURE).title


def test_imported_case_is_visible_through_normal_repository_and_samples_unchanged(tmp_path: Path) -> None:
    repository = CaseRepository(tmp_path / "cases")
    state = load_scale_fixture_case_state(FIXTURE).model_copy(update={"case_id": "scale-10-working"})
    repository.save(state)
    workflow = HumanEvidenceWorkflow(repository)
    assert "scale-10-working" in repository.list_case_ids()
    assert [item["title"] for item in sample_cases()] == [
        "Law Exam Investigation", "Multi-Candidate Collaboration Review"
    ]
    seed_sample_case(workflow, "multi-candidate", SAMPLE_CASE_IDS["multi-candidate"])
    public_sample = repository.load(SAMPLE_CASE_IDS["multi-candidate"])
    assert len(public_sample.subjects) == 5
    assert len(public_sample.sources) == 14


def test_imported_run_input_matches_canonical_fixture_semantics_and_prompt_is_blind(tmp_path: Path) -> None:
    state = load_scale_fixture_case_state(FIXTURE)
    repository = CaseRepository(tmp_path / "cases")
    imported = state.model_copy(update={"case_id": "scale-10-working"})
    repository.save(imported)
    imported = repository.load("scale-10-working")
    expected = run_input_from_case_state(state, academic_integrity_core_preset())
    actual = run_input_from_case_state(imported, academic_integrity_core_preset())
    expected_dump = expected.model_dump(mode="json")
    actual_dump = actual.model_dump(mode="json")
    expected_dump["case_id"] = actual_dump["case_id"]
    assert actual_dump == expected_dump

    prompt = build_prompt(actual)
    for source in actual.sources.values():
        assert prompt.count(f'"source_id": "{source.id}"') == 1
    assert all(f"Candidate {letter}" in prompt for letter in "ABCDEFGHIJ")
    assert '"participants": [\n      "subject_A",\n      "subject_B"' in prompt
    assert '"participants": [\n      "subject_F",\n      "subject_G"' in prompt
    hidden = [path.read_text(encoding="utf-8") for path in (FIXTURE / "evaluator_only").glob("*")]
    assert not any(text in prompt for text in hidden)
    assert not any("evaluator_only" in source.name for source in actual.sources.values())


def test_imported_case_replays_existing_hand_authored_pipeline() -> None:
    module_path = ROOT / "tests/test_vnext_next_step_and_scale_fixture.py"
    spec = importlib.util.spec_from_file_location("scale_tests", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = load_scale_fixture_case_state(FIXTURE)
    run_input = run_input_from_case_state(state, academic_integrity_core_preset())
    compiled = module.compile_semantic_assessment(module._scale_assessment(state), run_input)
    result = module.VNextInvestigationRunner(lambda _: compiled).run(run_input)
    report = build_report_record(build_input_snapshot(state, academic_integrity_core_preset(), "imported"), result, completed_at="offline")
    assert len(result.subject_assessments) == 10
    assert sum(len(item.violation_assessments) for item in result.subject_assessments) == 40
    assert all(item.suggested_next_step for item in result.subject_assessments)
    assert {item["subject_id"] for item in report["students"]} == {f"subject_{letter}" for letter in "ABCDEFGHIJ"}
