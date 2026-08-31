from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EnquiryAction:
    action_id: str
    title: str
    definition: str
    artifact_filename: str
    source_type: str


ARTIFACTS_DIR = Path(__file__).parent / "artifacts"

ENQUIRY_CATALOG = (
    EnquiryAction(
        "A1", "Verify tutoring claim",
        "Independently verify whether the claimed intensive tutoring during the previous week occurred and what was covered.",
        "A1_tutoring_verification_packet.md", "record_and_source_statement",
    ),
    EnquiryAction(
        "A2", "Review behaviour and question timing",
        "Analyse the examination event record to determine whether the unusual jaw/ear-touching and pencil-case movements correlate with question difficulty, long pauses, or answer timing.",
        "A2_exam_event_log.xlsx", "event_log",
    ),
    EnquiryAction(
        "A3", "Compare with prior observed behaviour",
        "Review prior-observation statements to assess whether the current movement pattern resembles previously observed nervous or habitual behaviour.",
        "A3_prior_behaviour_statements.md", "witness_statements",
    ),
    EnquiryAction(
        "A4", "Targeted oral explanation",
        "Review a targeted assessor-student interview about difficult questions that were answered correctly with little or no visible written working.",
        "A4_student_interview_record.md", "interview_record",
    ),
)

_BY_ID = {action.action_id: action for action in ENQUIRY_CATALOG}


def get_action(action_id: str) -> EnquiryAction:
    try:
        return _BY_ID[action_id]
    except KeyError as exc:
        raise ValueError(f"Invalid enquiry action ID: {action_id!r}") from exc


def artifact_path(action_id: str) -> Path:
    action = get_action(action_id)
    path = ARTIFACTS_DIR / action.artifact_filename
    if not path.is_file():
        raise FileNotFoundError(f"Missing artefact for {action_id}: {path}")
    return path

