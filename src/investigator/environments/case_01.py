import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from investigator.environments.base import InvestigationEnvironment
from investigator.models import EvidenceItem, EvidenceKind, Source, SourceType
from investigator.services.contracts import InitialResponse, NextActionResponse, RevisionResponse
from investigator.state import CaseState, build_initial_state, build_seeded_initial_state


@dataclass(frozen=True)
class ControlledAction:
    action_id: str
    title: str
    definition: str
    artifact_filename: str
    source_type: str


CASE_01_EVIDENCE = (
    "E1. The student scored 40/40 and submitted after 57 minutes.",
    "E2. Examination-room footage shows no visible phone, no communication with another candidate, and no repeated looking toward another desk.",
    "E3. On 11 occasions, the student touched the right side of their jaw or ear and repositioned the pencil case with the other hand. Most of these movements occurred after the student had spent at least 20 seconds reading a question.",
    "E4. The student answered several computational questions without writing visible calculations. Mental calculation was possible, although these were among the more difficult questions.",
    "E5. A pre-entry bag check found no prohibited object. The student was not searched after the examination.",
    "E6. An invigilator who supervised an earlier quiz recalls that the student frequently tapped the desk and adjusted their glasses when nervous, but does not remember the jaw-touching pattern.",
    "E7. The student says they had received intensive private tutoring during the previous week and that the movements were ordinary anxiety-related fidgeting. No information about the tutor has yet been obtained.",
)
CASE_01_ASSESSMENT_CONTEXT = "90-minute closed-book quantitative methods examination. 40 multiple-choice questions. Phones and smartwatches had to be surrendered before entry. Ordinary stationery and opaque pencil cases were permitted. The student scored between 52% and 64% on four earlier timed quizzes. Homework median: 66%."

CASE_01_ACTIONS = (
    ControlledAction("A1", "Verify tutoring claim", "Independently verify whether the claimed intensive tutoring during the previous week occurred and what was covered.", "A1_tutoring_verification_packet.md", "record_and_source_statement"),
    ControlledAction("A2", "Review behaviour and question timing", "Analyse the examination event record to determine whether the unusual jaw/ear-touching and pencil-case movements correlate with question difficulty, long pauses, or answer timing.", "A2_exam_event_log.xlsx", "event_log"),
    ControlledAction("A3", "Compare with prior observed behaviour", "Review prior-observation statements to assess whether the current movement pattern resembles previously observed nervous or habitual behaviour.", "A3_prior_behaviour_statements.md", "witness_statements"),
    ControlledAction("A4", "Targeted oral explanation", "Review a targeted assessor-student interview about difficult questions that were answered correctly with little or no visible written working.", "A4_student_interview_record.md", "interview_record"),
)


class Case1ControlledEnvironment(InvestigationEnvironment):
    environment_id = "case_01_controlled"
    case_id = "case_01"

    def __init__(self, assets_root: str | Path) -> None:
        self.assets_root = Path(assets_root).resolve()
        self._actions = {action.action_id: action for action in CASE_01_ACTIONS}

    def initial_case_input(self) -> str:
        return "CASE 1 — Closed-book physical examination\n\nAssessment context:\n" + CASE_01_ASSESSMENT_CONTEXT + "\n\nEvidence:\n" + "\n".join(CASE_01_EVIDENCE)

    def initial_prompt(self) -> str:
        from investigator.environments.case_01_prompts import initial_prompt
        return initial_prompt(self)

    def initial_expansion_prompt(self, seed_statement: str) -> str:
        from investigator.environments.case_01_prompts import initial_expansion_prompt
        return initial_expansion_prompt(self, seed_statement)

    def assessment_context(self) -> str:
        from investigator.prompts import render_assessment_context
        return render_assessment_context()

    def build_initial_state(self, response: InitialResponse) -> CaseState:
        source = Source(id="case_01_visible", name="Case 01 visible evidence", source_type=SourceType.OTHER)
        evidence = {
            f"E{index}": EvidenceItem(id=f"E{index}", source_id=source.id, raw_content=content, kind=EvidenceKind.OTHER)
            for index, content in enumerate(CASE_01_EVIDENCE, start=1)
        }
        return build_initial_state(self.case_id, "Closed-book physical examination", {source.id: source}, evidence, response)

    def build_seeded_initial_state(self, seed_statement: str, response) -> CaseState:
        source = Source(id="case_01_visible", name="Case 01 visible evidence", source_type=SourceType.OTHER)
        evidence = {f"E{index}": EvidenceItem(id=f"E{index}", source_id=source.id, raw_content=content, kind=EvidenceKind.OTHER) for index, content in enumerate(CASE_01_EVIDENCE, start=1)}
        return build_seeded_initial_state(self.case_id, "Closed-book physical examination", {source.id: source}, evidence, seed_statement, response.seed_analysis, response.competing_hypotheses)

    def available_actions(self, completed_action_ids: set[str]) -> list[ControlledAction]:
        return [action for action in CASE_01_ACTIONS if action.action_id not in completed_action_ids]

    def get_action(self, action_id: str) -> ControlledAction:
        try:
            return self._actions[action_id]
        except KeyError as exc:
            raise ValueError(f"Invalid enquiry action ID: {action_id!r}") from exc

    def execute_action(self, state: CaseState, action_id: str):
        from investigator.environments.case_01_artifacts import render_artifact
        action = self.get_action(action_id)
        path = self.assets_root / action.artifact_filename
        if not path.is_file():
            raise FileNotFoundError(f"Missing artefact for {action_id}: {path}")
        source = Source(id=f"{action_id}_source", name=action.title, source_type=SourceType.OTHER, metadata={"source_type": action.source_type})
        evidence_id = f"{action_id}_RELEASE"
        state.sources[source.id] = source
        state.evidence[evidence_id] = EvidenceItem(id=evidence_id, source_id=source.id, raw_content=render_artifact(path), kind=EvidenceKind.OTHER, metadata={"action_id": action_id, "artifact": action.artifact_filename})
        from investigator.services.contracts import ReleaseRecord
        return ReleaseRecord(action_id=action_id, artifact_id=evidence_id, artifact_path=str(path), source_type=action.source_type, content=state.evidence[evidence_id].raw_content)

    def revision_prompt(self, session: Any, release: Any) -> str:
        from investigator.environments.case_01_prompts import revision_prompt
        return revision_prompt(self, session, release)

    def next_action_prompt(self, session: Any, available_actions: list[ControlledAction]) -> str:
        from investigator.environments.case_01_prompts import next_action_prompt
        return next_action_prompt(self, session, available_actions)
