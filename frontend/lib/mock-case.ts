import type { CaseWorkspaceState } from "./types";

export const initialWorkspace: CaseWorkspaceState = {
  caseId: "case-01",
  title: "Law Exam Investigation",
  status: "waiting_for_evidence",
  institutionalStatus: "Investigating",
  currentFocus: "Whether external assistance could explain the unusual assessment pattern.",
  messages: [
    {
      id: "message-01",
      role: "investigator",
      timestamp: "10:21 AM",
      text: "Reviewed the assessment script and invigilator report.",
    },
    {
      id: "message-02",
      role: "simplifynext",
      timestamp: "10:22 AM",
      text: "The available material leaves one important question open.",
      request: {
        request_id: "request-01",
        informationSought: "Obtain available evidence of Candidate A's device or communication activity during the assessment period.",
        reason: "This could help determine whether the observed behaviour was associated with external information access.",
        status: "pending",
      },
    },
  ],
  sources: [
    { sourceHandle: "demo-source-1", fileName: "Assessment paper", documentFormat: "md" },
    { sourceHandle: "demo-source-2", fileName: "Student script", documentFormat: "md" },
    { sourceHandle: "demo-source-3", fileName: "Assessment rules", documentFormat: "md" },
    { sourceHandle: "demo-source-4", fileName: "Marker report", documentFormat: "md" },
    { sourceHandle: "demo-source-5", fileName: "Invigilator report", documentFormat: "md" },
    { sourceHandle: "demo-source-6", fileName: "Assessment logistics", documentFormat: "md" },
  ],
  students: [{ studentHandle: "demo-student-1", displayName: "Student 1", candidateNumber: null }],
  report: { state: "unavailable", assessmentIsStale: true },
  assessment: { state: "not_started", activeRun: null, latestAttempt: null, reportAvailable: false, reportStale: false },
  chatHistory: [],
  activity: [],
  caseStatus: "ACTIVE",
  runtimeStatus: "WAITING_FOR_EVIDENCE",
  currentActor: "NONE",
  caseKind: "user",
  sample: null,
  capabilities: { editStudents: true, addEvidence: true, resetSample: false, runAssessment: true, useHelp: true, viewSources: true },
  preloadedSourceCount: 0,
};
