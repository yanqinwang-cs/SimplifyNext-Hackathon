from dataclasses import dataclass
from pathlib import Path

from investigator.models.source import Source, SourceType
from investigator.state import CaseState


ROOT = Path(__file__).parent
VISIBLE_KEYS = ("blank_tutorial", "student_script", "marker_report", "invigilator_report", "assessment_rules", "assessment_logistics")
VISIBLE_FILENAMES = ("blank_tutorial.md", "student_script.md", "marker_report.md", "invigilator_report.md", "assessment_rules.md", "assessment_logistics.md")
HIDDEN_KEYS = ("entry_items", "prior_work", "student_support", "course_materials", "module_scope", "student_clarification", "device_examination", "smart_glasses_activity", "network_connectivity", "device_account_linkage", "external_ai_service", "outside_assistant")


@dataclass(frozen=True)
class Fixture:
    key: str
    filename: str
    concepts: tuple[str, ...]
    content: str


def _fixture(key: str, filename: str, concepts: tuple[str, ...], content: str) -> Fixture:
    return Fixture(key, filename, concepts, content)


def hidden_fixtures() -> tuple[Fixture, ...]:
    contents = {
        "entry_items": "Entry record: ordinary permitted materials and personal items were recorded before the assessment.",
        "prior_work": "Prior assessed work is available as a baseline for Candidate A's earlier performance.",
        "student_support": "A support session record describes prior assistance and preparation.",
        "course_materials": "Course materials contain the assigned authorities and tutorial content.",
        "module_scope": "The module scope syllabus describes the assessed teaching scope.",
        "student_clarification": "Candidate A's clarification records the account given about which eyewear was worn.",
        "device_examination": "Device examination record: the relevant eyewear capabilities were examined.",
        "smart_glasses_activity": "Activity record: available usage events for the identified eyewear during the assessment.",
        "network_connectivity": "Connectivity record: available Wi-Fi and internet activity associated with the assessment period.",
        "device_account_linkage": "Linkage record: available evidence concerning device sessions and a phone or account.",
        "external_ai_service": "External service record: available requests relating to assessment content.",
        "outside_assistant": "Communication record: available communications with someone outside the assessment.",
    }
    concepts = {
        "entry_items": ("items", "belongings", "permitted", "bring", "entry"),
        "prior_work": ("prior", "earlier", "previous", "baseline", "assessed work", "performance"),
        "student_support": ("support", "tutoring", "preparation", "assistance"),
        "course_materials": ("course", "materials", "authority", "assigned"),
        "module_scope": ("scope", "syllabus", "covered", "teaching"),
        "student_clarification": ("student", "explanation", "account", "worn", "which glasses"),
        "device_examination": ("eyewear", "glasses", "capabilities", "record", "transmit", "receive", "electronic", "functionality"),
        "smart_glasses_activity": ("activity", "usage", "logs", "session", "device", "eyewear"),
        "network_connectivity": ("network", "wifi", "wi-fi", "internet", "connectivity"),
        "device_account_linkage": ("link", "linked", "pairing", "phone", "account", "associated"),
        "external_ai_service": ("external service", "online service", "ai", "requests", "received"),
        "outside_assistant": ("outside", "communications", "communication", "classmate", "person"),
    }
    filenames = {
        "entry_items": "entry_permitted_items_record.md", "prior_work": "prior_assessed_work.md", "student_support": "student_support_session.md", "course_materials": "course_materials.md", "module_scope": "module_scope_syllabus.md", "student_clarification": "student_clarification_record.md", "device_examination": "device_examination_report.md", "smart_glasses_activity": "smart_glasses_activity_record.md", "network_connectivity": "network_connectivity_record.md", "device_account_linkage": "device_account_linkage_record.md", "external_ai_service": "external_ai_service_record.md", "outside_assistant": "potential_outside_assistant_record.md",
    }
    return tuple(_fixture(key, filenames[key], concepts[key], contents[key]) for key in HIDDEN_KEYS)


def initial_case() -> CaseState:
    state = CaseState(case_id="stage2b-case-01", title="Business Law Tutorial 5")
    contents = {
        "blank_tutorial": "The blank tutorial is the neutral assessment record supplied for this case.",
        "student_script": "The student script contains the submitted assessment response.",
        "marker_report": "The marker report records the specific concerns and anomalies observed in the submitted work.",
        "invigilator_report": "The invigilator report records ordinary observations about assessment conduct.",
        "assessment_rules": "The assessment rules describe the permitted assessment conditions.",
        "assessment_logistics": "The assessment logistics describe the assessment period and location.",
    }
    for index, key in enumerate(VISIBLE_KEYS, start=1):
        state.sources[f"S{index}"] = Source(id=f"S{index}", name=VISIBLE_FILENAMES[index - 1], source_type=SourceType.DOCUMENT, content=contents[key])
    return state


def fixture_root() -> Path:
    return ROOT / "fixtures"
