import { test, expect, type Page } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";

test.describe.configure({ mode: "serial" });

const repoRoot = resolve(process.cwd(), "..");
let backend: ChildProcess | undefined;
let testRepository: string;
const backendPort = process.env.CASE5A_BACKEND_PORT ?? "8003";
const backendBase = `http://127.0.0.1:${backendPort}`;
const frontendApiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

async function waitForBackend(): Promise<void> {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      if ((await fetch(`${backendBase}/api/cases`)).ok) return;
    } catch {
      // The backend is still starting.
    }
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 100));
  }
  throw new Error("Case 5A audit backend did not start");
}

async function createCase(page: Page, title: string): Promise<string> {
  await page.goto("/cases");
  await page.getByPlaceholder("Case name").fill(title);
  await page.getByRole("button", { name: "Create case" }).click();
  await expect(page).toHaveURL(/\/cases\/case-\d+/);
  return new URL(page.url()).pathname.split("/").pop() as string;
}

async function addEvidence(page: Page, fileName: string, content: string): Promise<void> {
  await page.locator('input[type="file"]').setInputFiles({ name: fileName, mimeType: "text/plain", buffer: Buffer.from(content) });
  await page.getByRole("button", { name: "Add evidence" }).click();
}

test.beforeAll(async () => {
  testRepository = mkdtempSync(join(tmpdir(), "simplifynext-case5a-audit-browser-"));
  backend = spawn("uv", ["run", "python", "scripts/stage7_fake_server.py", "--repository", testRepository, "--port", backendPort], {
    cwd: repoRoot,
    env: {
      ...process.env,
      AWS_ACCESS_KEY_ID: "PRE5A_AUDIT_ACCESS",
      AWS_SECRET_ACCESS_KEY: "PRE5A_AUDIT_SECRET",
      AWS_SESSION_TOKEN: "PRE5A_AUDIT_SESSION",
      AWS_EC2_METADATA_DISABLED: "true",
      SIMPLIFYNEXT_RUN_MODE: "vnext",
      SIMPLIFYNEXT_ALLOWED_ORIGINS: "http://127.0.0.1:3000",
      SIMPLIFYNEXT_DEBUG_CREDENTIALS: "0",
      STAGE7_FAKE_DELAY_SECONDS: "0",
      STAGE7_FAKE_TIMEOUT: "1",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });
  await waitForBackend();
});

test.afterAll(async () => {
  if (backend && !backend.killed) backend.kill("SIGTERM");
  await new Promise<void>((resolvePromise) => backend?.once("exit", () => resolvePromise()) ?? resolvePromise());
  rmSync(testRepository, { recursive: true, force: true });
});

test.beforeEach(async ({ page }) => {
  // The full browser suite also runs the Stage 7 backend on port 8000. Keep
  // this focused audit backend isolated without changing production API code.
  await page.route(`${frontendApiBase}/**`, (route) => route.continue({ url: route.request().url().replace(frontendApiBase, backendBase) }));
});

test("successful run exposes the trace download and complete trace", async ({ page }, testInfo) => {
  await createCase(page, "Case 5A successful audit");
  await addEvidence(page, "success-record.txt", "A controlled record.");
  await page.getByRole("button", { name: "Run assessment" }).click();
  await expect(page.getByText("Assessment complete. The current report is up to date.")).toBeVisible({ timeout: 30_000 });
  await expect(page.locator("pre")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Download trace" })).toBeVisible();
  await expect(page.locator("pre")).toHaveCount(0);
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download trace" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/^caselens-case-\d+-assessment-[a-f0-9]+-trace\.jsonl$/);
  const tracePath = testInfo.outputPath("audit-trace.jsonl");
  await download.saveAs(tracePath);
  const traceLines = readFileSync(tracePath, "utf-8").trim().split("\n").map((line) => JSON.parse(line));
  expect(traceLines.some((item) => item.event === "vnext_completed")).toBeTruthy();
  const modelCallStarted = traceLines.find((item) => item.event === "vnext_model_call_started");
  const modelCallCompleted = traceLines.find((item) => item.event === "vnext_model_call_completed");
  expect(modelCallStarted).toMatchObject({ model_call_number: 1, prompt: expect.any(String) });
  expect(modelCallCompleted).toMatchObject({ model_call_number: 1, raw_output: expect.anything(), parsed_output: expect.anything() });
  expect(traceLines.some((item) => Object.hasOwn(item, "result"))).toBeTruthy();
  await expect(page.locator("body")).not.toContainText("PRE5A_AUDIT_");
});

test("provider timeout stays failed, is not retried, and remains downloadable as a trace", async ({ page }, testInfo) => {
  await createCase(page, "Case 5A timeout audit");
  await addEvidence(page, "timeout-record.txt", "TIMEOUT_SENTINEL");
  await page.getByRole("button", { name: "Run assessment" }).click();
  await expect(page.getByRole("paragraph").filter({ hasText: "Assessment could not be completed because the model provider did not return a response in time." })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("link", { name: "View report" })).toHaveCount(0);
  await expect(page.locator("pre")).toHaveCount(0);
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download trace" }).click();
  const download = await downloadPromise;
  const tracePath = testInfo.outputPath("timeout-audit-trace.jsonl");
  await download.saveAs(tracePath);
  const trace = readFileSync(tracePath, "utf-8");
  expect(trace).toContain("PROVIDER_TIMEOUT");
  expect(trace).toContain("ReadTimeoutError");
  expect(page.locator("pre")).toHaveCount(0);
  await expect(page.getByText("PRE5A_AUDIT_", { exact: false })).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText("PRE5A_AUDIT_");
});
