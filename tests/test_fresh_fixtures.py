import hashlib
import json

from experiments.steward_screen.fresh_fixtures import fresh_fixtures
from experiments.steward_screen.trajectory import ScriptedProducer, run_fixture


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
        [d("archive", target_node_id="P2"), d("reactivate", target_node_id="H3"), d("shift_focus", destination_node_id="H3")],
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
