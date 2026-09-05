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
    env: { ...process.env, AWS_EC2_METADATA_DISABLED: "true", SIMPLIFYNEXT_RUN_MODE: "vnext", SIMPLIFYNEXT_DEBUG_CREDENTIALS: "0" },
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
