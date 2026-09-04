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
  visibleSources: [
    { id: "S1", name: "Assessment paper", sourceType: "document", content: "", contentPreview: "" },
    { id: "S2", name: "Student script", sourceType: "document", content: "", contentPreview: "" },
    { id: "S3", name: "Assessment rules", sourceType: "document", content: "", contentPreview: "" },
    { id: "S4", name: "Marker report", sourceType: "document", content: "", contentPreview: "" },
    { id: "S5", name: "Invigilator report", sourceType: "document", content: "", contentPreview: "" },
    { id: "S6", name: "Assessment logistics", sourceType: "document", content: "", contentPreview: "" },
  ],
  caseStatus: "ACTIVE",
  runtimeStatus: "WAITING_FOR_EVIDENCE",
  currentActor: "NONE",
};
