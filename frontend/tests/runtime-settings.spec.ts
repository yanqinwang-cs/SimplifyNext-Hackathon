import { test, expect } from "@playwright/test";

const sonnet = "anthropic.claude-sonnet-4-5";
const opus = "anthropic.claude-opus-4-5";

function settings(model = sonnet, help = sonnet, temporary = false) {
  return { aws: { mode: temporary ? "temporary_credentials" : "default_chain", statusLabel: temporary ? "Temporary AWS credentials loaded" : "Default AWS credential chain", lastUpdatedAt: null, region: "us-east-1" }, models: { investigator: { effectiveModel: model, effectiveLabel: model === opus ? "Claude Opus 4.5" : "Claude Sonnet 4.5", source: model === sonnet ? "default" : "runtime_selection", lastUsed: null, noModelCallRequired: false }, workspaceHelp: { effectiveModel: help, effectiveLabel: help === opus ? "Claude Opus 4.5" : "Claude Sonnet 4.5", source: help === sonnet ? "default" : "runtime_selection", lastUsed: null } }, availableModels: [{ model: sonnet, label: "Claude Sonnet 4.5" }, { model: opus, label: "Claude Opus 4.5" }] };
}

test("Runtime settings supports safe credentials, draft/apply/reset, and storage redaction", async ({ page }, testInfo) => {
  let current = settings();
  await page.route("**/api/cases/case-01/workspace", (route) => route.fulfill({ json: { caseId: "case-01", caseRevision: 0, title: "Offline case", status: "ready", institutionalStatus: "Investigating", currentFocus: "", messages: [], caseStatus: "ACTIVE", caseKind: "user", sample: null, capabilities: { editStudents: true, addEvidence: true, resetSample: false, runAssessment: true, useHelp: true, viewSources: true }, preloadedSourceCount: 0, runtimeStatus: "IDLE", currentActor: "NONE", sources: [], students: [{ studentHandle: "s1", displayName: "Student 1" }], report: { state: "unavailable", assessmentIsStale: false }, assessment: { state: "not_started", activeRun: null, latestAttempt: null, reportAvailable: false, reportStale: false }, chatHistory: [], activity: [] } }));
  await page.route("**/api/cases/case-01/report", (route) => route.fulfill({ json: { caseId: "case-01", title: "Offline case", reportState: "unavailable", assessmentIsStale: false, latestSuccessfulRun: null, students: [] } }));
  await page.route("**/api/runtime-settings**", async (route) => {
    const request = route.request(); const url = new URL(request.url());
    if (request.method() === "GET") return route.fulfill({ json: current });
    if (url.pathname.endsWith("/aws-credentials")) { current = settings(current.models.investigator.effectiveModel, current.models.workspaceHelp.effectiveModel, request.method() === "POST"); return route.fulfill({ json: current }); }
    if (url.pathname.endsWith("/models/reset")) { const aws = current.aws; current = { ...settings(), aws }; return route.fulfill({ json: current }); }
    if (url.pathname.endsWith("/models")) { const body = request.postDataJSON(); const aws = current.aws; current = { ...settings(body.investigator, body.workspaceHelp), aws }; return route.fulfill({ json: current }); }
    return route.fulfill({ status: 404, json: { error: "not found" } });
  });

  await page.goto("/cases/case-01");
  await page.getByRole("button", { name: "Runtime settings" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByText("Default AWS credential chain")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("runtime-default.png"), fullPage: true });
  await page.getByLabel("AWS Access Key ID").fill("FAKE_ACCESS_SECRET");
  await page.getByLabel("AWS Secret Access Key").fill("FAKE_SECRET_VALUE");
  await page.getByLabel("AWS Session Token").fill("FAKE_TOKEN_VALUE");
  await page.getByRole("button", { name: "Apply temporary credentials" }).click();
  await expect(page.getByText("Temporary AWS credentials loaded for this running session.")).toBeVisible();
  await expect(page.getByLabel("AWS Access Key ID")).toHaveValue("");
  await expect(page.locator("body")).not.toContainText("FAKE_ACCESS_SECRET");
  await page.screenshot({ path: testInfo.outputPath("runtime-credentials-loaded.png"), fullPage: true });
  const storage = await page.evaluate(() => ({ local: JSON.stringify(localStorage), session: JSON.stringify(sessionStorage), url: location.href, cookies: document.cookie }));
  expect(JSON.stringify(storage)).not.toContain("FAKE_");
  await page.getByLabel("Investigator model").selectOption(opus);
  await expect(page.getByText("Pending change")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("runtime-pending-model.png"), fullPage: true });
  await page.getByRole("button", { name: "Apply model settings" }).click();
  await expect(page.getByText("Model settings applied for subsequent calls.")).toBeVisible();
  await expect(page.getByText("Effective: Claude Opus 4.5")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("runtime-applied-opus.png"), fullPage: true });
  await page.getByRole("button", { name: "Reset defaults" }).click();
  await expect(page.getByText("Model defaults restored.")).toBeVisible();
  await expect(page.getByLabel("Investigator model")).toHaveValue(sonnet);
  await expect(page.getByText("Effective: Claude Sonnet 4.5").first()).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("runtime-reset-default.png"), fullPage: true });
  await page.getByRole("button", { name: "Clear temporary credentials" }).click();
  await expect(page.getByText("Temporary credentials cleared. The default AWS credential chain will be used.")).toBeVisible();
  await page.getByRole("button", { name: "Close Runtime settings" }).click();
  await expect(page.getByRole("dialog")).toBeHidden();
});
