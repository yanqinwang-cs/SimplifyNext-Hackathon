import { test, expect } from "@playwright/test";

const guide = `# Investigation Assistant\n\n## Create a case\n\n1. Enter a case name.\n2. A new case begins with **Student 1**.\n\n- Add readable .txt or .md evidence.\n\nThere is no minimum evidence count. Final institutional judgment remains human.`;

function workspace(title = "Original case", sample = false, chatHistory: { role: string; text: string }[] = []) {
  return { caseId: "case-01", caseRevision: 0, title, status: "ready", institutionalStatus: "Investigating", currentFocus: "", messages: [], caseStatus: "ACTIVE", caseKind: sample ? "sample" : "user", sample: sample ? { sampleId: "law-exam", title: "Law Exam Investigation" } : null, capabilities: { editStudents: !sample, addEvidence: !sample, resetSample: sample, runAssessment: true, useHelp: true, viewSources: true }, preloadedSourceCount: 0, runtimeStatus: "IDLE", currentActor: "NONE", sources: [{ sourceHandle: "source-public", fileName: "record.md", documentFormat: "markdown" }], students: sample ? ["A", "B", "C", "D", "E"].map((letter) => ({ studentHandle: `student-${letter}`, displayName: `Candidate ${letter}` })) : [{ studentHandle: "student-public", displayName: "Student 1" }], report: { state: "unavailable", assessmentIsStale: false }, assessment: { state: "not_started", activeRun: null, latestAttempt: null, reportAvailable: false, reportStale: false }, chatHistory, activity: [] };
}

test("guide renders authoritative markdown with current workflow", async ({ page }, testInfo) => {
  await page.route("**/api/product-guide", (route) => route.fulfill({ status: 200, contentType: "text/markdown", body: guide }));
  await page.goto("/help");
  await expect(page.getByRole("heading", { name: "Investigation Assistant" })).toBeVisible();
  await expect(page.getByRole("list").first()).toBeVisible();
  await expect(page.locator("pre")).toHaveCount(0);
  await expect(page.getByText("Student 1")).toBeVisible();
  await expect(page.getByText("Final institutional judgment remains human.")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("guide-rendered.png"), fullPage: true });
  await page.getByRole("link", { name: "← Cases" }).click();
  await expect(page).toHaveURL(/\/cases$/);
});

test("user case title editing and Help quick prompts use isolated normal paths", async ({ page }, testInfo) => {
  let current = workspace();
  await page.route("**/api/cases/case-01/workspace", (route) => route.fulfill({ json: current }));
  await page.route("**/api/cases/case-01/report", (route) => route.fulfill({ json: { caseId: "case-01", title: current.title, reportState: "unavailable", assessmentIsStale: false, latestSuccessfulRun: null, students: [] } }));
  await page.route("**/api/cases/case-01", async (route) => {
    const body = route.request().postDataJSON?.() ?? {};
    if (route.request().method() === "PATCH") { current = workspace(body.title); return route.fulfill({ json: { workspace: current } }); }
    return route.fulfill({ status: 404, json: { error: "not found" } });
  });
  await page.route("**/api/cases/case-01/workspace/chat", async (route) => {
    current = workspace(current.title, false, [{ role: "human", text: "What remains uncertain?" }, { role: "workspace", text: "The current record leaves one uncertainty unresolved." }]);
    return route.fulfill({ json: { response: "The current record leaves one uncertainty unresolved.", actions: [], recovery: false } });
  });
  await page.goto("/cases/case-01");
  await page.getByRole("button", { name: "Edit" }).click();
  await page.getByLabel("Case name").fill("Renamed case");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("heading", { name: "Renamed case" })).toBeVisible();
  await expect(page.getByText("Assessment status")).toBeVisible();
  await page.getByRole("button", { name: "What remains uncertain?" }).click();
  await expect(page.getByText("The current record leaves one uncertainty unresolved.")).toBeVisible();
  await expect(page.getByText("Assessment running")).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("workspace-help-and-title.png"), fullPage: true });
});

test("sample case keeps its fixed name and has no edit control", async ({ page }, testInfo) => {
  const sample = workspace("Law Exam Investigation", true);
  await page.route("**/api/cases/case-01/workspace", (route) => route.fulfill({ json: sample }));
  await page.route("**/api/cases/case-01/report", (route) => route.fulfill({ json: { caseId: "case-01", title: sample.title, reportState: "unavailable", assessmentIsStale: false, latestSuccessfulRun: null, students: [] } }));
  await page.goto("/cases/case-01");
  await expect(page.getByRole("heading", { name: "Law Exam Investigation" })).toBeVisible();
  await expect(page.getByText("Sample", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Edit" })).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("sample-fixed-name.png"), fullPage: true });
});
