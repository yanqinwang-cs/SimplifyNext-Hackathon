COMMON_INSTRUCTION = """Use only the supplied case information. Generate 2–4 plausible explanations.
Keep each explanation specific and concise. Briefly justify each explanation using supplied evidence.
State one important uncertainty or limitation for each explanation. Distinguish observations from possible explanations.
Do not invent missing events, sources, communications, tools, motives, devices, timestamps, or records.
Do not decide whether misconduct occurred or propose investigative actions.
Prefer a small number of plausible explanations over exhaustive speculation.
Return only JSON matching this schema: {"hypotheses": [{"statement": "...", "justification": "...", "uncertainty": "..."}]}"""


def build_prompt(case_input: str) -> str:
    return f"{COMMON_INSTRUCTION}\n\nCase information:\n{case_input}"
