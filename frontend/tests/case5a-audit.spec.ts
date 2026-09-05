import { test, expect, type Page } from "@playwright/test";
import { spawn, type ChildProcess } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";

test.describe.configure({ mode: "serial" });

const repoRoot = resolve(process.cwd(), "..");
let backend: ChildProcess | undefined;
let testRepository: string;
const backendBase = "http://127.0.0.1:8003";

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
  backend = spawn("uv", ["run", "python", "scripts/stage7_fake_server.py", "--repository", testRepository, "--port", "8003"], {
    cwd: repoRoot,
    env: {
      ...process.env,
      AWS_ACCESS_KEY_ID: "PRE5A_AUDIT_ACCESS",
      AWS_SECRET_ACCESS_KEY: "PRE5A_AUDIT_SECRET",
      AWS_SESSION_TOKEN: "PRE5A_AUDIT_SESSION",
      AWS_EC2_METADATA_DISABLED: "true",
      SIMPLIFYNEXT_RUN_MODE: "vnext",
      SIMPLIFYNEXT_ENABLE_DIAGNOSTIC_API: "1",
      SIMPLIFYNEXT_ALLOWED_ORIGINS: "http://127.0.0.1:3007",
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

test("successful run exposes a compact operator audit trace", async ({ page }) => {
  await createCase(page, "Case 5A successful audit");
  await addEvidence(page, "success-record.txt", "A controlled record.");
  await page.getByRole("button", { name: "Run assessment" }).click();
  await expect(page.getByText("Assessment complete. The current report is up to date.")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "View audit trace" }).click();
  await expect(page.getByRole("heading", { name: "Assessment audit" })).toBeVisible();
  await expect(page.getByText("completed", { exact: true })).toBeVisible();
  await expect(page.locator("pre")).toContainText("vnext_completed");
  await expect(page.locator("body")).not.toContainText("PRE5A_AUDIT_");
});

test("provider timeout stays failed, is not retried, and is visible only in the audit surface", async ({ page }) => {
  await createCase(page, "Case 5A timeout audit");
  await addEvidence(page, "timeout-record.txt", "TIMEOUT_SENTINEL");
  await page.getByRole("button", { name: "Run assessment" }).click();
  await expect(page.getByRole("paragraph").filter({ hasText: "Assessment could not be completed because the model provider did not return a response in time." })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("link", { name: "View report" })).toHaveCount(0);
  await page.getByRole("button", { name: "View audit trace" }).click();
  await expect(page.getByRole("heading", { name: "Assessment audit" })).toBeVisible();
  await expect(page.getByText("failed", { exact: true })).toBeVisible();
  await expect(page.locator("pre")).toContainText("PROVIDER_TIMEOUT");
  await expect(page.locator("pre")).toContainText("ReadTimeoutError");
  await expect(page.getByText("modelCalls", { exact: true })).toHaveCount(0);
  await expect(page.getByText("PRE5A_AUDIT_", { exact: false })).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText("PRE5A_AUDIT_");
});
