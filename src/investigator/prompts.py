GLOBAL_INVESTIGATION_RULES = """Use only released evidence. Previous hypotheses are not evidence and must not be cited as support. Reason about actions attributable to the student; knowledge, opportunity, anomaly, possession, and unusual performance do not by themselves establish misconduct. Own proficiency is distinct from external assistance, and permission depends on the assessment rule, capability, and time."""


def render_assessment_context() -> str:
    return "90-minute closed-book quantitative methods examination; 40 multiple-choice questions; held at an examination venue. Own proficiency includes memory, reasoning, mental calculation, movement, and permitted object manipulation. Ordinary pre-exam tutoring is permitted; assistance during the examination and external communication are prohibited. Ordinary stationery and an opaque pencil case are permitted. Phones and smartwatches must be surrendered before entry. Context defines policy and capabilities, not whether a resource was used; location alone does not establish misconduct."


def render_current_case(case_input: str, hypotheses: list[dict], uncertainties: list[dict]) -> str:
    return f"{case_input}\n\nPrior hypotheses/tree (NOT evidence):\n{__import__('json').dumps(hypotheses, indent=2)}\n\nPrior unresolved uncertainties:\n{__import__('json').dumps(uncertainties, indent=2)}"
