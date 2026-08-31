DEMO_MESSAGES = [
    {"role": "assistant", "content": "I found two plausible explanations and one important uncertainty."},
    {"role": "user", "content": "Do not treat E7 as corroboration of anxiety."},
    {"role": "assistant", "content": "Understood. E7 will be treated as a student claim only."},
]

ACTIVE_HYPOTHESES = [
    ("H1", "Permitted preparation may explain performance."),
    ("H2", "Prohibited assistance may have contributed."),
]

KEY_UNCERTAINTIES = [
    ("U1", "Meaning of repeated movements"),
    ("U2", "Whether preparation explains the performance gap"),
]

CLAIMS_TO_CHECK = [
    ("C1", "Tutoring occurred", "Uncorroborated"),
    ("C2", "Movements were anxiety-related", "Uncorroborated"),
]

EVIDENCE = [
    ("E1", "Examination performance was unusually strong."),
    ("E2", "Repeated jaw, ear, and pencil-case movements were observed."),
    ("E3", "Some difficult questions were answered with little visible working."),
    ("E4", "The examination was closed-book and held at an examination venue."),
    ("E5", "Ordinary stationery and an opaque pencil case were permitted."),
    ("E6", "A phone or smartwatch was required to be surrendered before entry."),
    ("E7", "The student said the movements were anxiety-related."),
]

RELEASED_EVIDENCE = []

RECOMMENDED_ACTION = {
    "id": "A2",
    "label": "Review behaviour and timing",
    "explanation": "This may help distinguish between the current explanations.",
}
