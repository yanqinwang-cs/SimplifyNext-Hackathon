"""Compatibility exports for the Case 1 environment action catalogue."""

from pathlib import Path

from investigator.environments.case_01 import CASE_01_ACTIONS, ControlledAction

ENQUIRY_CATALOG = CASE_01_ACTIONS
ARTIFACTS_DIR = Path(__file__).parent / "artifacts"


def get_action(action_id: str) -> ControlledAction:
    for action in ENQUIRY_CATALOG:
        if action.action_id == action_id:
            return action
    raise ValueError(f"Invalid enquiry action ID: {action_id!r}")


def artifact_path(action_id: str) -> Path:
    action = get_action(action_id)
    path = ARTIFACTS_DIR / action.artifact_filename
    if not path.is_file():
        raise FileNotFoundError(f"Missing artefact for {action_id}: {path}")
    return path
