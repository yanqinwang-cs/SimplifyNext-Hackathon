import { test, expect, type Page } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";

test.describe.configure({ mode: "serial" });

const repoRoot = resolve(process.cwd(), "..");
let backend: ChildProcess | undefined;
let testRepository: string;

async function waitForBackend(): Promise<void> {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch("http://127.0.0.1:8000/api/cases");
      if (response.ok) return;
    } catch {
      // The backend is still starting.
    }
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 100));
  }
  throw new Error("Stage 7 fake backend did not start");
}

async function createCase(page: Page, title: string): Promise<string> {
  await page.goto("/cases");
  await page.getByPlaceholder("Case name").fill(title);
  await page.getByRole("button", { name: "Create case" }).click();
  await expect(page).toHaveURL(/\/cases\/case-\d+/);
  return new URL(page.url()).pathname.split("/").pop() as string;
}

function providerCalls(): Array<{ kind: string; model: string; region: string }> {
  return JSON.parse(readFileSync(join(testRepository, ".stage7-provider-calls.json"), "utf-8"));
}

test.beforeAll(async () => {
  testRepository = mkdtempSync(join(tmpdir(), "simplifynext-stage7-browser-"));
  backend = spawn("uv", ["run", "python", "scripts/stage7_fake_server.py", "--repository", testRepository], {
    cwd: repoRoot,
    env: { ...process.env, AWS_EC2_METADATA_DISABLED: "true", SIMPLIFYNEXT_RUN_MODE: "vnext", SIMPLIFYNEXT_DEBUG_CREDENTIALS: "0", STAGE7_FAKE_DELAY_SECONDS: "0.4" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  await waitForBackend();
});

test.afterAll(async () => {
  if (backend && !backend.killed) backend.kill("SIGTERM");
  await new Promise<void>((resolvePromise) => backend?.once("exit", () => resolvePromise()) ?? resolvePromise());
  rmSync(testRepository, { recursive: true, force: true });
});

test("real case creation keeps independent Student 1 state and enforces the final-student guard", async ({ page }, testInfo) => {
  const first = await createCase(page, "Acceptance case A");
  await expect(page.getByText("Student 1", { exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("real-user-case.png"), fullPage: true });
  await page.getByRole("button", { name: "What remains uncertain?" }).click();
  await expect(page.getByText("controlled Workspace Help provider returned a read-only response.")).toBeVisible();
  await page.getByLabel("Add student").fill("Student 2");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByText("Student 2", { exact: true })).toBeVisible();
  await page.once("dialog", (dialog) => dialog.accept("Renamed Student"));
  await page.getByRole("button", { name: "Rename" }).first().click();
  await expect(page.getByText("Renamed Student", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Remove" }).last().click();
  await expect(page.getByText("Student 2", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Remove" })).toBeDisabled();

  const second = await createCase(page, "Acceptance case B");
  expect(second).not.toBe(first);
  await expect(page.getByText("Student 1", { exact: true })).toBeVisible();
  await expect(page.getByText("Renamed Student", { exact: true })).toHaveCount(0);
  const secondWorkspace = await page.request.get(`http://127.0.0.1:8000/api/cases/${second}/workspace`);
  expect((await secondWorkspace.json()).students).toHaveLength(1);
});

test("real upload and source reader preserve admitted text without leaking it in workspace", async ({ page }, testInfo) => {
  const caseId = await createCase(page, "Evidence acceptance case");
  const content = "# Unicode heading\n\nAccented — text and a second line.";
  await page.locator('input[type="file"]').setInputFiles({ name: "unicode-evidence.md", mimeType: "text/markdown", buffer: Buffer.from(content) });
  await page.getByRole("button", { name: "Add evidence" }).click();
  await expect(page.getByRole("link", { name: /unicode-evidence\.md/ })).toBeVisible();
  const workspaceResponse = await page.request.get(`http://127.0.0.1:8000/api/cases/${caseId}/workspace`);
  expect(JSON.stringify(await workspaceResponse.json())).not.toContain(content);
  await page.getByRole("link", { name: /unicode-evidence\.md/ }).click();
  await expect(page.getByRole("heading", { name: "unicode-evidence.md" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Unicode heading" })).toBeVisible();
  await expect(page.getByText("Accented — text and a second line.")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("real-source-reader.png"), fullPage: true });
  await page.getByRole("link", { name: "← Back to case" }).click();
  await expect(page).toHaveURL(new RegExp(`/cases/${caseId}$`));
});

test("real vNext zero-evidence and fake-provider assessments publish independent reports", async ({ page }, testInfo) => {
  const zeroCase = await createCase(page, "Zero evidence acceptance case");
  await page.getByRole("button", { name: "Run assessment" }).click();
  await expect(page.getByText("Assessment complete. The current report is up to date.")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("link", { name: "View report" })).toBeVisible();
  expect(providerCalls().filter((call) => call.kind === "structured")).toHaveLength(0);
  await page.getByRole("link", { name: "View report" }).click();
  await expect(page.getByRole("heading", { name: "Findings by student" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Student 1" })).toBeVisible();
  await expect(page.getByText("Not currently supported").first()).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("real-zero-evidence-report.png"), fullPage: true });

  const modelCase = await createCase(page, "Model boundary acceptance case");
  await page.locator('input[type="file"]').setInputFiles({ name: "record.txt", mimeType: "text/plain", buffer: Buffer.from("A controlled record.") });
  await page.getByRole("button", { name: "Add evidence" }).click();
  await page.getByRole("button", { name: "Run assessment" }).click();
  await expect(page.getByText("Assessment complete. The current report is up to date.")).toBeVisible({ timeout: 30_000 });
  expect(providerCalls().filter((call) => call.kind === "structured")).toHaveLength(1);
  expect(providerCalls().every((call) => call.model.includes("anthropic.claude") || call.model === "stage7.fake")).toBeTruthy();
  await expect(page.getByRole("link", { name: "View report" })).toBeVisible();
  expect(zeroCase).not.toBe(modelCase);
});

test("real status observation failure retains the same run for Retry", async ({ page }) => {
  const caseId = await createCase(page, "Polling recovery case");
  await page.locator('input[type="file"]').setInputFiles({ name: "polling-record.txt", mimeType: "text/plain", buffer: Buffer.from("A delayed controlled record.") });
  await page.getByRole("button", { name: "Add evidence" }).click();
  let startPosts = 0;
  let failedStatusRequests = 0;
  const statusHandles = new Set<string>();
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (request.method() === "POST" && url.pathname === `/api/cases/${caseId}/run`) startPosts += 1;
    if (url.pathname.includes("/assessment-runs/")) statusHandles.add(url.pathname.split("/").pop() ?? "");
  });
  await page.route("**/api/cases/*/assessment-runs/*", async (route) => {
    if (failedStatusRequests === 0) {
      failedStatusRequests += 1;
      await route.abort("failed");
      return;
    }
    await route.continue();
  });
  await page.getByRole("button", { name: "Run assessment" }).click();
  await expect(page.getByText("Could not refresh assessment status. Retry the status check.")).toBeVisible({ timeout: 30_000 });
  expect(startPosts).toBe(1);
  await expect(page.getByRole("button", { name: "Retry status check" })).toBeVisible();
  await page.unroute("**/api/cases/*/assessment-runs/*");
  await page.getByRole("button", { name: "Retry status check" }).click();
  await expect(page.getByText("Assessment complete. The current report is up to date.")).toBeVisible({ timeout: 30_000 });
  expect(startPosts).toBe(1);
  expect(statusHandles.size).toBe(1);
  await expect(page.getByRole("button", { name: "Retry status check" })).toHaveCount(0);
});

test("real Runtime settings uses the actual HTTP backend without changing case state or exposing secrets", async ({ page }, testInfo) => {
  const caseId = await createCase(page, "Runtime settings case");
  await page.request.delete("http://127.0.0.1:8000/api/runtime-settings/aws-credentials");
  await page.request.post("http://127.0.0.1:8000/api/runtime-settings/models/reset", { data: {} });
  const before = await page.request.get(`http://127.0.0.1:8000/api/cases/${caseId}/workspace`);
  const beforeWorkspace = await before.json();
  const providerCount = providerCalls().length;
  await page.getByRole("button", { name: "Runtime settings" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.locator('select[aria-label="Investigator model"] option')).toHaveCount(2);
  await expect(page.locator('select[aria-label="Workspace Help model"] option')).toHaveCount(2);
  await page.getByLabel("AWS Access Key ID").fill("PRE5A_FAKE_ACCESS");
  await page.getByLabel("AWS Secret Access Key").fill("PRE5A_FAKE_SECRET");
  await page.getByLabel("AWS Session Token").fill("PRE5A_FAKE_SESSION");
  await page.getByRole("button", { name: "Apply temporary credentials" }).click();
  await expect(page.getByText("Temporary AWS credentials loaded for this running session.")).toBeVisible();
  await expect(page.getByLabel("AWS Access Key ID")).toHaveValue("");
  await expect(page.getByLabel("AWS Secret Access Key")).toHaveValue("");
  await expect(page.getByLabel("AWS Session Token")).toHaveValue("");
  await expect(page.locator("body")).not.toContainText("PRE5A_FAKE_");
  await page.screenshot({ path: testInfo.outputPath("runtime-credentials-loaded-real-backend.png"), fullPage: true });
  await page.getByRole("button", { name: "Close Runtime settings" }).click();
  await page.getByRole("button", { name: "Runtime settings" }).click();
  await expect(page.getByLabel("AWS Access Key ID")).toHaveValue("");
  await page.getByRole("button", { name: "Clear temporary credentials" }).click();
  await expect(page.getByText("Temporary credentials cleared. The default AWS credential chain will be used.")).toBeVisible();
  await page.getByLabel("Investigator model").selectOption("anthropic.claude-opus-4-5");
  await expect(page.getByText("Pending change")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("runtime-model-pending-real-backend.png"), fullPage: true });
  await page.getByRole("button", { name: "Apply model settings" }).click();
  await expect(page.getByText("Model settings applied for subsequent calls.")).toBeVisible();
  await page.getByLabel("Workspace Help model").selectOption("anthropic.claude-opus-4-5");
  await page.getByRole("button", { name: "Apply model settings" }).click();
  await expect(page.getByText("Model settings applied for subsequent calls.")).toBeVisible();
  const applied = await page.request.get("http://127.0.0.1:8000/api/runtime-settings");
  const appliedSettings = await applied.json();
  expect(appliedSettings.models.investigator.effectiveModel).toBe("anthropic.claude-opus-4-5");
  expect(appliedSettings.models.workspaceHelp.effectiveModel).toBe("anthropic.claude-opus-4-5");
  await page.screenshot({ path: testInfo.outputPath("runtime-model-applied-real-backend.png"), fullPage: true });
  await page.getByRole("button", { name: "Reset defaults" }).click();
  await expect(page.getByText("Model defaults restored.")).toBeVisible();
  const reset = await page.request.get("http://127.0.0.1:8000/api/runtime-settings");
  const resetSettings = await reset.json();
  expect(resetSettings.models.investigator.effectiveModel).toBe("anthropic.claude-sonnet-4-5");
  expect(resetSettings.models.workspaceHelp.effectiveModel).toBe("anthropic.claude-sonnet-4-5");
  await page.screenshot({ path: testInfo.outputPath("runtime-model-reset-real-backend.png"), fullPage: true });
  const storage = await page.evaluate(() => ({ local: JSON.stringify(localStorage), session: JSON.stringify(sessionStorage), url: location.href }));
  expect(JSON.stringify(storage)).not.toContain("PRE5A_FAKE_");
  const after = await page.request.get(`http://127.0.0.1:8000/api/cases/${caseId}/workspace`);
  expect((await after.json()).caseRevision).toBe(beforeWorkspace.caseRevision);
  expect(providerCalls()).toHaveLength(providerCount);
  await expect(page.locator("body")).not.toContainText("Failed to fetch");
});

test("real sample boundaries and negative HTTP paths remain safe", async ({ page }, testInfo) => {
  await page.goto("/cases");
  await page.getByRole("button", { name: "Law Exam Investigation" }).click();
  await expect(page.getByText("Law Exam Investigation", { exact: true })).toBeVisible();
  await expect(page.getByText("Candidate A", { exact: true })).toBeVisible();
  await expect(page.locator('input[type="file"]')).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("real-law-sample.png"), fullPage: true });
  await page.setViewportSize({ width: 700, height: 900 });
  await expect(page.getByRole("heading", { name: "Law Exam Investigation" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("real-law-sample-narrow.png"), fullPage: true });
  await page.goto("/cases");
  await page.getByRole("button", { name: "Multi-Candidate Collaboration Review" }).click();
  for (const letter of ["A", "B", "C", "D", "E"]) await expect(page.getByText(`Candidate ${letter}`, { exact: true })).toBeVisible();
  const unknown = await page.request.get("http://127.0.0.1:8000/api/cases/case-does-not-exist/workspace");
  expect(unknown.status()).toBe(404);
  const traversal = await page.request.get("http://127.0.0.1:8000/api/cases/%2E%2E%2Fcase-000001/workspace");
  expect(traversal.status()).toBe(404);
  const disallowed = await page.request.get("http://127.0.0.1:8000/api/cases", { headers: { Origin: "https://not-allowed.example" } });
  expect(disallowed.status()).toBe(403);
  const disallowedPreflight = await page.request.fetch("http://127.0.0.1:8000/api/cases", { method: "OPTIONS", headers: { Origin: "https://not-allowed.example", "Access-Control-Request-Method": "DELETE" } });
  expect(disallowedPreflight.status()).toBe(403);
});
