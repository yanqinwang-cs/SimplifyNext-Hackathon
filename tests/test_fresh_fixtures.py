import hashlib
import json
from copy import deepcopy
import pytest

from experiments.steward_screen.audit_artifacts import hidden_audit_payload, public_audit_payload
from experiments.steward_screen.fresh_fixtures import fresh_fixtures
from experiments.steward_screen.prompt import build_prompt
from experiments.steward_screen.trajectory import ScriptedProducer, TrajectoryFixture, run_fixture, validate_public_observation_distinguishability


def d(op, **kw):
    return {"operation": op, "assessment": "controlled", "reason": "controlled", **kw}


def test_fresh_suite_has_eight_distinct_valid_fixtures():
    fixtures = fresh_fixtures()
    assert [f.fixture_id for f in fixtures] == [f"SEQ{i}" for i in range(1, 9)]
    assert len({f.graph.model_dump_json() for f in fixtures}) == 8


def test_every_fresh_fixture_has_a_scripted_success_path():
    paths = [
        [d("archive", target_node_id="H2"), d("shift_focus", destination_node_id="H3")],
        [d("reactivate", target_node_id="H2"), d("shift_focus", destination_node_id="H2")],
        [d("generalize", target_node_id="H1.1"), d("shift_focus", destination_node_id="H2")],
        [d("archive", target_node_id="H1", destination_node_id="H2")],
        [d("archive", target_node_id="H2")],
        [d("archive", target_node_id="H1", destination_node_id="U1"), d("stop_unresolved", important_unresolved_ids=["U1"], reopening_conditions="new evidence")],
        [d("keep_focus")],
        [d("archive", target_node_id="H2"), d("reactivate", target_node_id="H3"), d("shift_focus", destination_node_id="H3")],
    ]
    for fixture, path in zip(fresh_fixtures(), paths):
        result = run_fixture(fixture, ScriptedProducer(path))
        assert not result.failures, (fixture.fixture_id, result)
        assert result.termination in {"quiescent", "stopped"}


def test_frozen_fixture_fingerprint_is_reproducible():
    payload = [f.model_dump(mode="json") for f in fresh_fixtures()]
    first = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    second = hashlib.sha256(json.dumps([f.model_dump(mode="json") for f in fresh_fixtures()], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert first == second


def test_repaired_alternate_orderings_and_public_basis():
    fixtures = {item.fixture_id: item for item in fresh_fixtures()}
    paths = {
        "SEQ1": [d("shift_focus", destination_node_id="H3"), d("archive", target_node_id="H2")],
        "SEQ3": [d("shift_focus", destination_node_id="H2"), d("generalize", target_node_id="H1.1")],
        "SEQ8": [d("reactivate", target_node_id="H3"), d("archive", target_node_id="H2"), d("shift_focus", destination_node_id="H3")],
    }
    for fixture_id, path in paths.items():
        result = run_fixture(fixtures[fixture_id], ScriptedProducer(path))
        assert not result.failures, (fixture_id, result)
    assert all(item.basis_audit_required for item in fixtures.values())


def test_old_identical_public_seq4_seq5_pattern_is_rejected():
    fixtures = fresh_fixtures()
    seq4, seq5 = fixtures[3], fixtures[4]
    duplicate = seq5.model_copy(update={"description": seq4.description, "graph": deepcopy(seq4.graph), "focus": seq4.focus.model_copy(deep=True)})
    with pytest.raises(ValueError, match="share a public observation"):
        validate_public_observation_distinguishability([seq4, duplicate])


def test_public_audit_artifact_excludes_hidden_key_and_prompts_exclude_basis():
    public, hidden = public_audit_payload(), hidden_audit_payload()
    assert all("issues" not in item and "public_basis" not in item for item in public["fixtures"])
    assert all("issues" in item and "public_basis" in item for item in hidden["fixtures"])
    for fixture in fresh_fixtures():
        prompt = build_prompt(fixture.observation())
        assert all(issue.issue_id not in prompt for issue in fixture.issues)
        assert all(basis.basis_id not in prompt for basis in fixture.public_basis)


def test_basis_metadata_requires_declared_basis_for_audited_fixture():
    fixture = next(item for item in fresh_fixtures() if item.fixture_id == "SEQ5")
    payload = fixture.model_dump(mode="python")
    payload["public_basis"] = []
    with pytest.raises(ValueError, match="no declared public basis"):
        TrajectoryFixture.model_validate(payload)
