from typing import TypedDict


class ScreenCase(TypedDict):
    case_id: str
    title: str
    assessment_context: str
    evidence: list[str]


CASES: list[ScreenCase] = [
    {
        "case_id": "case_01",
        "title": "Closed-book physical examination",
        "assessment_context": "90-minute closed-book quantitative methods examination. 40 multiple-choice questions. Phones and smartwatches had to be surrendered before entry. Ordinary stationery and opaque pencil cases were permitted. The student scored between 52% and 64% on four earlier timed quizzes. Homework median: 66%.",
        "evidence": [
            "E1. The student scored 40/40 and submitted after 57 minutes.",
            "E2. Examination-room footage shows no visible phone, no communication with another candidate, and no repeated looking toward another desk.",
            "E3. On 11 occasions, the student touched the right side of their jaw or ear and repositioned the pencil case with the other hand. Most of these movements occurred after the student had spent at least 20 seconds reading a question.",
            "E4. The student answered several computational questions without writing visible calculations. Mental calculation was possible, although these were among the more difficult questions.",
            "E5. A pre-entry bag check found no prohibited object. The student was not searched after the examination.",
            "E6. An invigilator who supervised an earlier quiz recalls that the student frequently tapped the desk and adjusted their glasses when nervous, but does not remember the jaw-touching pattern.",
            "E7. The student says they had received intensive private tutoring during the previous week and that the movements were ordinary anxiety-related fidgeting. No information about the tutor has yet been obtained.",
        ],
    },
    {
        "case_id": "case_02",
        "title": "Take-home programming lab",
        "assessment_context": "Individual 72-hour programming lab. Official documentation, lecture notes, and ordinary non-generative autocomplete were allowed. Generative systems that produced or rewrote code were prohibited. Students were required to submit Git history. Task involved processing a directed network and identifying compatible cycles.",
        "evidence": [
            "E1. The repository contains 23 incremental commits over two days. Most show incomplete implementations, test failures, and small corrections.",
            "E2. One 47-line helper function appears in a single commit after a 36-minute gap. Four small changes to that function appear in later commits.",
            "E3. The helper uses compact recursive memoisation and one pruning condition not covered in the course. The rest of the submission resembles the student’s earlier work in naming, formatting, and control-flow style.",
            "E4. No close match was found in public-source search, the course repository, or other students’ submissions.",
            "E5. An experimental code-authorship detector marked the helper as “71% AI-likely.” Its own documentation states that the result is an indicator rather than proof.",
            "E6. During a short oral check, the student correctly explains the program’s overall design and successfully modifies input validation. They initially give an incorrect explanation for why the helper’s pruning condition is safe, but identify the problem after being shown a counterexample.",
            "E7. The student says they wrote the helper in a separate scratch file after reading documentation, then pasted it into the repository once it worked.",
        ],
    },
    {
        "case_id": "case_03",
        "title": "Individual laboratory reports with permitted discussion",
        "assessment_context": "Students A, B, and C attended the same engineering laboratory session and used the same apparatus. Sharing raw measurements was permitted. Discussing the general method was permitted. Sharing derived calculations, spreadsheets, or report wording was prohibited. The supplied spreadsheet template contained only raw-data columns and standard headings.",
        "evidence": [
            "E1. All three reports use the same permitted raw measurements.",
            "E2. A and B’s spreadsheets contain the same custom labels, `drift_adj` and `rho_corr`, which were not present in the supplied template.",
            "E3. A and B use the same hidden intermediate precision and contain the same transposed-cell error in one derived formula.",
            "E4. Two sentences in A and B’s method discussions are nearly identical. Their other prose differs.",
            "E5. C’s spreadsheet layout, labels, formulas, and prose are different. However, C reaches the same incorrect sign for the final correction term.",
            "E6. Messaging metadata shows 27 messages in a group chat involving all three students on the evening before submission. Message contents are unavailable.",
            "E7. B submitted 92 minutes before A. C submitted approximately five hours before B.",
            "E8. A says the group “compared results but did not exchange files.” B says they “only shared the permitted measurements.” C says they discussed which calibration value applied but completed the calculations independently.",
            "E9. No file-transfer records are available.",
        ],
    },
    {
        "case_id": "case_04",
        "title": "Research paper with external editing",
        "assessment_context": "2,000-word individual policy brief. Students could obtain grammar, spelling, and formatting feedback. External assistance could not rewrite sentences, restructure reasoning, or produce substantive prose. External proofreading had to be declared. The student made no declaration.",
        "evidence": [
            "E1. Document history shows ten days of gradual work under the student’s account, including an outline, source notes, and early versions of every major argument.",
            "E2. Thirty-six hours before submission, an account labelled `Reviewer-3` replaced 612 words across two sections during a 14-minute period.",
            "E3. The replacement preserves the same claims, evidence, citations, and paragraph order, but uses more polished language and substantially stronger transitions.",
            "E4. All cited sources exist and support the claims attributed to them. No meaningful text match was found on the public web or in the submission database.",
            "E5. The student’s earlier assignments generally use shorter sentences and simpler transitions. Other sections of the current paper also show some improvement, although less sharply.",
            "E6. In an oral discussion, the student accurately explains the brief’s argument, evidence, and source selection.",
            "E7. The student says `Reviewer-3` was a family friend who “only corrected grammar.” The account owner has not been identified, and the original tracked changes are unavailable.",
        ],
    },
    {
        "case_id": "case_05",
        "title": "Timed online quiz",
        "assessment_context": "20-question online quiz with a 25-minute time limit. Closed-book during the attempt. Students had unlimited access beforehand to an official practice quiz. Use of official practice material was expressly permitted. External answer services, communication, and unauthorised devices were prohibited during the graded attempt.",
        "evidence": [
            "E1. The student had averaged 58% across previous graded quizzes but scored 20/20 in 6 minutes 12 seconds.",
            "E2. Fourteen responses were submitted within three to six seconds of the question appearing. The other six took between 15 and 35 seconds.",
            "E3. LMS logs show no tab switching during the attempt. The logs cannot establish whether another device was used.",
            "E4. The student completed the official practice quiz 19 times over nine days. Their recorded practice score rose from 55% to 100%. The last attempt ended approximately 40 minutes before the graded quiz.",
            "E5. The student says that many graded questions felt familiar from the official practice quiz.",
            "E6. Another student scored 20/20 in 7 minutes 3 seconds. The two students were in different tutorial groups, used different networks, and have no known communication history.",
            "E7. The instructor says that the practice and graded quizzes were built from the same broad topic bank, but has not compared the exact questions.",
        ],
    },
]


def render_case(case: ScreenCase) -> str:
    evidence = "\n".join(case["evidence"])
    return f"CASE {case['case_id'].removeprefix('case_')} — {case['title']}\n\nAssessment context:\n{case['assessment_context']}\n\nEvidence:\n{evidence}"


def get_case(case_id: str) -> ScreenCase:
    for case in CASES:
        if case["case_id"] == case_id:
            return case
    raise KeyError(f"Unknown model-screen case: {case_id!r}")

