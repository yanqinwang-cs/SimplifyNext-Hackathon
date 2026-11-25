import { test, expect, type Page } from "@playwright/test";

type Overrides = Record<string, unknown>;

function workspace(overrides: Overrides = {}) {
  return {
    caseId: "case-01",
    caseRevision: 0,
    title: "Review case",
    caseStatus: "ACTIVE",
    caseKind: "user",
    sample: null,
    capabilities: {
      editStudents: true,
      addEvidence: true,
      resetSample: false,
      runAssessment: true,
      useHelp: true,
      viewSources: true,
    },
    preloadedSourceCount: 0,
    runtimeStatus: "IDLE",
    sources: [],
    students: [{ studentHandle: "student-1", displayName: "Student 1" }],
    report: { state: "unavailable", assessmentIsStale: false },
    assessment: {
      state: "not_started",
      activeRun: null,
      latestAttempt: null,
      reportAvailable: false,
      reportStale: false,
    },
    chatHistory: [],
    activity: [],
    ...overrides,
  };
}

const noReport = {
  caseId: "case-01",
  currentCaseName: "Review case",
  reportState: "unavailable",
  assessmentIsStale: false,
  isLatestSuccessfulAssessment: true,
  assessment: null,
};

async function mockCase(page: Page, current: () => unknown) {
  await page.route("**/api/cases/case-01/workspace", (route) => route.fulfill({ json: current() }));
  await page.route("**/api/cases/case-01/report", (route) => route.fulfill({ json: noReport }));
}

test("guide paragraphs render bold and code instead of raw markers", async ({ page }) => {
  await page.route("**/api/product-guide", (route) =>
    route.fulfill({
      contentType: "text/markdown",
      body: "# Guide\n\nRead the **Evidence** list and run `Run assessment` next.",
    }),
  );
  await page.goto("/help");
  await expect(page.locator("p strong")).toHaveText("Evidence");
  await expect(page.locator("p code")).toHaveText("Run assessment");
  await expect(page.locator("p")).not.toContainText("**");
  await expect(page.locator("p")).not.toContainText("`");
});

test("toasts clear themselves and never show together", async ({ page }) => {
  let fail = false;
  let current = workspace();
  await mockCase(page, () => current);
  await page.route("**/api/cases/case-01", (route) => {
    if (fail) return route.fulfill({ status: 422, json: { error: "Invalid case name" } });
    current = workspace({ title: "Renamed" });
    return route.fulfill({ json: { workspace: current } });
  });
  await page.goto("/cases/case-01");
  await page.getByRole("button", { name: "Edit" }).click();
  await page.getByLabel("Case name").fill("Renamed");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Case name updated." })).toBeVisible();
  fail = true;
  await page.getByRole("button", { name: "Edit" }).click();
  await page.getByLabel("Case name").fill("Again");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Invalid case name" })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "Case name updated." })).toHaveCount(0);
  await expect(page.getByRole("alert").filter({ hasText: "Invalid case name" })).toHaveCount(0, {
    timeout: 10_000,
  });
});

test("new-case name input and evidence file input have accessible names", async ({ page }) => {
  await page.route("**/api/cases", (route) => route.fulfill({ json: { cases: [] } }));
  await page.route("**/api/samples", (route) => route.fulfill({ json: { samples: [] } }));
  await page.goto("/cases");
  await expect(page.getByLabel("Case name")).toBeVisible();
  await mockCase(page, () => workspace());
  await page.goto("/cases/case-01");
  await expect(page.getByLabel("Evidence file")).toBeVisible();
});

test("a failed run shows the public sentence the API sends", async ({ page }) => {
  const sentence =
    "The assessment could not be completed. Run it again; if it keeps failing, download the trace.";
  const current = workspace({
    assessment: {
      state: "failed_no_report",
      activeRun: null,
      reportAvailable: false,
      reportStale: false,
      latestAttempt: {
        runHandle: "run-1",
        state: "failed",
        startedAt: "2026-01-01T00:00:00Z",
        endedAt: "2026-01-01T00:01:00Z",
        traceReady: false,
        message: sentence,
      },
    },
  });
  await mockCase(page, () => current);
  await page.goto("/cases/case-01");
  await expect(page.getByText(sentence)).toBeVisible();
});

test("a late Help refresh cannot overwrite a finished assessment", async ({ page }) => {
  const idle = workspace();
  const complete = workspace({
    assessment: {
      state: "complete",
      activeRun: null,
      latestAttempt: null,
      reportAvailable: true,
      reportStale: false,
    },
  });
  const running = workspace({
    assessment: {
      state: "running",
      activeRun: { runHandle: "run-1", startedAt: "2026-01-01T00:00:00Z" },
      latestAttempt: null,
      reportAvailable: false,
      reportStale: false,
    },
  });
  let chatSent = false;
  let heldRefreshes = 0;
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => (release = resolve));
  await page.route("**/api/cases/case-01/workspace", async (route) => {
    if (!chatSent) return route.fulfill({ json: idle });
    heldRefreshes += 1;
    await gate;
    return route.fulfill({ json: idle });
  });
  await page.route("**/api/cases/case-01/report", (route) => route.fulfill({ json: noReport }));
  await page.route("**/api/cases/case-01/workspace/chat", (route) => {
    chatSent = true;
    return route.fulfill({ json: { response: "ok", actions: [], recovery: false } });
  });
  await page.route("**/api/cases/case-01/run", (route) =>
    route.fulfill({
      status: 202,
      json: { run: { runHandle: "run-1", state: "running", startedAt: "x" }, workspace: running },
    }),
  );
  await page.route("**/api/cases/case-01/assessment-runs/run-1", (route) =>
    route.fulfill({
      json: {
        runHandle: "run-1",
        state: "completed",
        startedAt: "x",
        endedAt: "y",
        message: "",
        reportAvailable: true,
        reportStale: false,
        workspace: complete,
      },
    }),
  );
  await page.goto("/cases/case-01");
  await page.getByRole("button", { name: "What remains uncertain?" }).click();
  await expect.poll(() => heldRefreshes).toBe(1);
  await page.getByRole("button", { name: "Run assessment" }).click();
  await expect(
    page.getByText("Assessment complete. The current report is up to date."),
  ).toBeVisible();
  release();
  await expect(page.getByRole("button", { name: "What remains uncertain?" })).toBeEnabled();
  await expect(
    page.getByText("Assessment complete. The current report is up to date."),
  ).toBeVisible();
});

test("Enter that confirms an input-method composition does not send Help", async ({ page }) => {
  const sent: string[] = [];
  await mockCase(page, () => workspace());
  await page.route("**/api/cases/case-01/workspace/chat", (route) => {
    sent.push(route.request().postDataJSON().message);
    return route.fulfill({ json: { response: "ok", actions: [], recovery: false } });
  });
  await page.goto("/cases/case-01");
  const input = page.getByLabel("Ask Workspace Help");
  await input.fill("ni hao");
  await input.dispatchEvent("keydown", { key: "Enter", isComposing: true });
  await input.dispatchEvent("keydown", { key: "Enter", keyCode: 229 });
  await page.waitForTimeout(300);
  expect(sent).toEqual([]);
  await input.press("Enter");
  await expect.poll(() => sent).toEqual(["ni hao"]);
});

test("the evidence file input is cleared after adding so the same file can be added again", async ({
  page,
}) => {
  await mockCase(page, () => workspace());
  await page.route("**/api/cases/case-01/sources", (route) =>
    route.fulfill({
      json: {
        source: { sourceHandle: "s", fileName: "note.txt", documentFormat: "text" },
        workspace: workspace(),
      },
    }),
  );
  await page.goto("/cases/case-01");
  const input = page.getByLabel("Evidence file");
  await input.setInputFiles({ name: "note.txt", mimeType: "text/plain", buffer: Buffer.from("x") });
  await page.getByRole("button", { name: "Add evidence" }).click();
  await expect(page.getByRole("button", { name: "Add evidence" })).toBeDisabled();
  await expect(input).toHaveValue("");
});

test("report status labels match the lowercase statuses the API sends", async ({ page }) => {
  const material = { statement: "s", sources: [] };
  await page.route("**/api/cases/case-01/report", (route) =>
    route.fulfill({
      json: {
        ...noReport,
        reportState: "available",
        assessment: {
          runHandle: "run-1",
          caseNameAtAssessment: "Review case",
          completedAt: "2026-01-01T00:00:00Z",
          students: [
            {
              sectionHandle: "sec-1",
              displayName: "Student 1",
              furthestConclusion: "x",
              violations: [
                {
                  label: "Collusion",
                  status: "partially_supported",
                  reasoningSummary: "r",
                  supportingMaterial: [material],
                  limitingMaterial: [],
                  unresolvedPoints: [],
                },
              ],
            },
          ],
        },
      },
    }),
  );
  await page.goto("/report/case-01");
  await expect(page.getByText("Partially supported", { exact: true })).toBeVisible();
});

test("quick navigation between two reports of one case never shows the older under the newer address", async ({
  page,
}) => {
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => (release = resolve));
  await page.route("**/api/cases/case-01/report**", async (route) => {
    const older = new URL(route.request().url()).searchParams.get("assessment") === "run-old";
    if (older) await gate;
    return route
      .fulfill({ json: { ...noReport, currentCaseName: older ? "Older run" : "Latest run" } })
      .catch(() => {});
  });
  await page.goto("/report/case-01?assessment=run-old");
  // Pushing before hydration finishes is ignored, so retry until the client router takes it.
  await expect
    .poll(
      async () => {
        await page.evaluate(() =>
          (window as unknown as { next: { router: { push(url: string): void } } }).next.router.push(
            "/report/case-01",
          ),
        );
        return page.url();
      },
      { timeout: 20_000 },
    )
    .not.toContain("assessment=");
  await expect(page.getByText("Latest run", { exact: true })).toBeVisible();
  release();
  await page.waitForTimeout(1000);
  await expect(page.getByText("Latest run", { exact: true })).toBeVisible();
  await expect(page.getByText("Older run", { exact: true })).toHaveCount(0);
});

test("student buttons have per-student names and the progress dots form a named group", async ({
  page,
}) => {
  await mockCase(page, () =>
    workspace({
      students: [
        { studentHandle: "student-1", displayName: "Student 1" },
        { studentHandle: "student-2", displayName: "Alice" },
      ],
    }),
  );
  await page.goto("/cases/case-01");
  await expect(page.getByRole("button", { name: "Rename Alice" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Remove Student 1" })).toBeVisible();
  await page.goto("/");
  await expect(page.getByRole("group", { name: "Introduction progress" })).toBeVisible();
});
