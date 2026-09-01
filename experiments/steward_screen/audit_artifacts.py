"""Generate separated public-observation and hidden-key audit artifacts."""
import json
from pathlib import Path
from typing import Any
from .fresh_fixtures import SUITE_VERSION, fresh_fixtures

def public_audit_payload() -> dict[str, Any]:
    return {"suite_version": SUITE_VERSION, "fixtures": [{"fixture_id": f.fixture_id, **f.public_observation_payload()} for f in fresh_fixtures()]}

def hidden_audit_payload() -> dict[str, Any]:
    return {"suite_version": SUITE_VERSION, "fixtures": [{"fixture_id": f.fixture_id, "issues": [i.model_dump(mode="json") for i in f.issues], "public_basis": [b.model_dump(mode="json") for b in f.public_basis], "must_remain_active_node_ids": sorted(f.must_remain_active_node_ids), "must_remain_archived_node_ids": sorted(f.must_remain_archived_node_ids), "terminal_mode": f.terminal_mode.value} for f in fresh_fixtures()]}

def write_audit_artifacts(output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    public_path, hidden_path = output_dir / f"{SUITE_VERSION}_public.json", output_dir / f"{SUITE_VERSION}_hidden.json"
    public_path.write_text(json.dumps(public_audit_payload(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    hidden_path.write_text(json.dumps(hidden_audit_payload(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return public_path, hidden_path

if __name__ == "__main__":
    for path in write_audit_artifacts(Path("experiments/steward_screen/fixture_audit")):
        print(path)
