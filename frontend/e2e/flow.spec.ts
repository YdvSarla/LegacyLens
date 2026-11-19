import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { FIXTURES, registerNewUser, uploadProject, waitUntilReady, watchForProblems, zipFixture } from "./helpers";

const SCREENSHOTS = process.env.E2E_SCREENSHOTS;

async function shot(page: Page, name: string) {
  if (SCREENSHOTS) await page.screenshot({ path: path.join(SCREENSHOTS, `${name}.png`), fullPage: true });
}

test("a new user can analyze a project and use every feature", async ({ page }) => {
  const problems = watchForProblems(page);

  await registerNewUser(page);
  await expect(page.getByText("Add your first project")).toBeVisible();
  await shot(page, "01-projects-empty");
  const id = await uploadProject(page, zipFixture("node_app"));

  await test.step("dashboard shows real analysis", async () => {
    await expect(page.getByText("Code quality score")).toBeVisible();
    await expect(page.getByText("Express", { exact: true })).toBeVisible();
    await expect(page.getByText("Lines of code by language")).toBeVisible();
    await expect(page.getByText("eval() runs arbitrary code.")).toBeVisible();
    await expect(page).toHaveTitle(/LegacyLens/);
    await shot(page, "02-dashboard");
  });

  await test.step("dashboard shortcuts lead to the right pages", async () => {
    await page.getByRole("link", { name: "Ask the assistant" }).click();
    await page.waitForURL(`**/projects/${id}/assistant`);
    await page.goBack();
    await page.getByRole("link", { name: "Compare commits" }).first().click();
    await page.waitForURL(`**/projects/${id}/compare`);
    await page.goBack();
  });

  await test.step("a code reference opens the right line", async () => {
    await page.getByRole("link", { name: /src\/services\/auth\.js:2/ }).first().click();
    await expect(page.getByRole("heading", { name: "src/services/auth.js" })).toBeVisible();
    await expect(page.getByTestId("line-highlight")).toBeVisible();
    await expect(page.locator("pre code")).toContainText("requireAuth");
    await shot(page, "03-code-viewer");
  });

  await test.step("file tree opens folders and finds files", async () => {
    await page.goto(`/projects/${id}/code`);
    await page.getByRole("button", { name: /^src/ }).click();
    await page.getByRole("button", { name: /^services/ }).click();
    await page.getByRole("link", { name: /mailer\.js/ }).click();
    await expect(page.getByRole("heading", { name: "src/services/mailer.js" })).toBeVisible();
    await page.getByLabel("Find a file").fill("userController");
    await expect(page.getByRole("link", { name: /src\/controllers\/userController\.js/ })).toBeVisible();
    await page.getByRole("button", { name: "Copy reference" }).click();
    await expect(page.getByRole("button", { name: "Copied" })).toBeVisible();
  });

  await test.step("architecture and inventory", async () => {
    await page.goto(`/projects/${id}/architecture`);
    await expect(page.getByText("Entry points")).toBeVisible();
    await expect(page.getByRole("link", { name: "src/server.js" })).toBeVisible();
    await shot(page, "04-architecture");

    await page.goto(`/projects/${id}/inventory?tab=endpoints`);
    await expect(page.getByText("/api/users", { exact: false }).first()).toBeVisible();
    await shot(page, "05-inventory-apis");
    await page.getByLabel("Search by path or handler").fill("health");
    await expect(page.getByText("/health")).toBeVisible();
    await expect(page.getByText("/api/users")).toHaveCount(0);
    await page.getByRole("tab", { name: "Functions and classes" }).click();
    await page.getByLabel("Search by name").fill("sendWelcome");
    await expect(page.getByText("sendWelcome").first()).toBeVisible();
    await page.getByLabel("Search by name").fill("nothing-has-this-name");
    await expect(page.getByText("Nothing matches this search")).toBeVisible();
    await page.getByRole("tab", { name: "Models" }).click();
    await expect(page.getByText("mongoose")).toBeVisible();
    await page.getByRole("tab", { name: "Dependencies" }).click();
    await expect(page.getByText("express", { exact: true })).toBeVisible();
    await page.getByRole("tab", { name: "Issues" }).click();
    await expect(page.getByText("dangerous-eval")).toBeVisible();
    await page.getByLabel("Filter by severity").selectOption("critical");
    await expect(page.getByText("No issues match these filters")).toBeVisible();
  });

  await test.step("interactive dependency graph", async () => {
    await page.goto(`/projects/${id}/graph`);
    const canvas = page.locator('[role="img"] canvas').first();
    await expect(canvas).toBeVisible();
    await page.getByLabel("Find a node").fill("mailer");
    await page.getByRole("button", { name: "src/services/mailer.js" }).click();
    await expect(page.getByText("Used by 2 file(s)")).toBeVisible();
    await shot(page, "06-graph");
    await page.getByRole("button", { name: "Focus here" }).click();
    await expect(page.getByText("Showing files within")).toBeVisible();
    await expect(page.getByText(/\d+ nodes, \d+ links/)).toBeVisible();
    await page.getByLabel("Focus depth").selectOption("1");
    await page.getByRole("button", { name: "Show all" }).click();
    await page.getByLabel("Layout").selectOption("breadthfirst");
    await page.getByRole("button", { name: "Fit graph to view" }).click();
    await page.getByLabel("Detail").selectOption("module");
    await expect(page.getByText(/\d+ nodes, \d+ links/)).toBeVisible();
    await page.getByLabel("Detail").selectOption("symbol");
    await expect(page.getByText(/\d+ nodes, \d+ links/)).toBeVisible();
    await page.getByLabel("Detail").selectOption("file");
    await page.getByLabel("Include tests").check();
    await page.getByText("Most connected nodes as a table").click();
    await expect(page.getByRole("button", { name: "test/user.test.js" })).toBeVisible();
  });

  await test.step("assistant answers with code references, and chats can be managed", async () => {
    await page.goto(`/projects/${id}/assistant`);
    await page.getByLabel("Your question").fill("Where is sendWelcome used?");
    await page.getByRole("button", { name: "Send" }).click();
    await expect(page.getByText("Answered without AI").or(page.getByText(/Answered by/)).first()).toBeVisible({ timeout: 200_000 });
    // A real AI provider may be configured, and then the answer's wording varies.
    const searched = page.locator("details:not([open]) summary", { hasText: "Code searched" });
    if (await searched.count()) await searched.first().click();
    const link = page.getByRole("link", { name: /mailer\.js:\d+/ }).first();
    await expect(link).toBeVisible();
    await shot(page, "07-assistant");
    await page.getByRole("button", { name: "New chat" }).click();
    await expect(page.getByText("Ask anything about this code")).toBeVisible();
    await page.getByRole("button", { name: /^Where is sendWelcome/ }).click();
    await expect(page.getByLabel("Your question")).toBeVisible();
    await expect(page.getByText("Where is sendWelcome used?").last()).toBeVisible();
    await page.getByRole("button", { name: /Delete chat/ }).click();
    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByRole("button", { name: /^Where is sendWelcome/ })).toBeVisible();
    await page.getByRole("button", { name: /Delete chat/ }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Delete chat" }).click();
    await expect(page.getByRole("button", { name: /^Where is sendWelcome/ })).toHaveCount(0);
  });

  await test.step("provider settings", async () => {
    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: "Google Gemini" })).toBeVisible();
    await expect(page.getByText("Keep your keys on the free tier.")).toBeVisible();
    await shot(page, "08-providers");
    const first = page.locator("h2", { hasText: /Gemini|Groq|Ollama/ }).first();
    const before = await first.innerText();
    await page.getByRole("button", { name: /^Move .* down$/ }).first().click();
    await expect(page.locator("h2", { hasText: /Gemini|Groq|Ollama/ }).first()).not.toHaveText(before);
    await page.getByRole("button", { name: "Restore default order" }).click();
    await expect(page.locator("h2", { hasText: /Gemini|Groq|Ollama/ }).first()).toHaveText(before);
  });

  await test.step("regression test runs in the sandbox, and can be edited and deleted", async () => {
    await page.goto(`/projects/${id}/tests`);
    await expect(page.getByText("Tests run in throwaway Docker containers")).toBeVisible();
    await page.getByLabel("File to test").fill("src/services/mailer.js");
    await page.getByRole("checkbox", { name: /Write behavior tests with AI/ }).uncheck();
    await page.getByRole("button", { name: "Generate test" }).click();
    await expect(page.getByLabel("Test code")).toHaveValue(/Baseline regression test/);
    await page.getByRole("button", { name: "Run in sandbox" }).click();
    await expect(page.getByText("2 passed, 0 failed", { exact: false })).toBeVisible({ timeout: 120_000 });
    await shot(page, "09-tests");

    const code = await page.getByLabel("Test code").inputValue();
    await page.getByLabel("Test code").fill(code.replace('["sendWelcome"', '["doesNotExist", "sendWelcome"'));
    await page.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("Test saved.")).toBeVisible();
    await page.getByRole("button", { name: "Run in sandbox" }).click();
    await expect(page.getByText("1 passed, 1 failed", { exact: false })).toBeVisible({ timeout: 120_000 });
    await expect(page.getByText("Sandbox output")).toBeVisible();

    await page.getByRole("button", { name: "Delete this test" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Delete test" }).click();
    await expect(page.getByText("No tests yet")).toBeVisible();
  });

  await test.step("documentation", async () => {
    await page.goto(`/projects/${id}/docs`);
    await expect(page.getByText("No documentation yet")).toBeVisible();
    await page.getByRole("checkbox", { name: "Add an AI introduction" }).uncheck();
    await page.getByRole("button", { name: "Generate documentation" }).click();
    await expect(page.getByRole("heading", { name: "HTTP API reference" })).toBeVisible({ timeout: 60_000 });
    const download = page.getByRole("link", { name: "Download Markdown" });
    await expect(download).toBeVisible();
    const file = await page.request.get((await download.getAttribute("href"))!);
    expect(await file.text()).toContain("## HTTP API reference");
    await page.getByRole("link", { name: "Dependencies" }).click();
    await expect(page).toHaveURL(/#dependencies$/);
    await shot(page, "10-docs");
    await page.getByRole("button", { name: "Delete this version" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Delete version" }).click();
    await expect(page.getByText("No documentation yet")).toBeVisible();
  });

  await test.step("project settings", async () => {
    await page.goto(`/projects/${id}/settings`);
    await page.getByLabel("Project name").fill("Renamed project");
    await page.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("Name saved.")).toBeVisible();
    await page.getByRole("checkbox", { name: /Use local AI only/ }).check();
    await expect(page.getByText("This project now uses local AI only.")).toBeVisible();
    await page.getByRole("checkbox", { name: /Use local AI only/ }).uncheck();
    await expect(page.getByText("Cloud AI providers are allowed again.")).toBeVisible();
    await page.getByRole("button", { name: "Analyze again without changes" }).click();
    await waitUntilReady(page, id);
  });

  await test.step("a new version can be compared with the old one", async () => {
    await page.goto(`/projects/${id}/compare`);
    await expect(page.getByText("There is only one version so far")).toBeVisible();
    await page.goto(`/projects/${id}/settings`);
    const mailer = readFileSync(path.join(FIXTURES, "node_app/src/services/mailer.js"), "utf8");
    const changed = mailer.replace("function sendWelcome(address) {", "function sendWelcome(address, locale) {") + "\neval(process.env.CODE);\n";
    await page.getByLabel("New ZIP version").setInputFiles(zipFixture("node_app", { "src/services/mailer.js": changed }));
    await page.getByLabel("Version label").fill("Release 2");
    await page.getByRole("button", { name: "Upload version" }).click();
    await expect(page.getByText("Upload received.")).toBeVisible();
    await waitUntilReady(page, id, 2);
    await page.goto(`/projects/${id}/compare`);
    await expect(page.getByRole("button", { name: "Compare", exact: true })).toBeEnabled();
    await page.getByRole("button", { name: "Compare", exact: true }).click();
    await expect(page.getByText("Change risk")).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText("The signature of sendWelcome changed")).toBeVisible();
    await expect(page.getByRole("link", { name: "src/routes/users.js" }).first()).toBeVisible();
    await shot(page, "11-compare");
    await page.reload();
    await page.getByRole("button", { name: /^[0-9a-f]{8} to [0-9a-f]{8}$/ }).first().click();
    await expect(page.getByText("Change risk")).toBeVisible();
  });

  await test.step("the project can be deleted", async () => {
    await page.goto(`/projects/${id}/settings`);
    await expect(page.getByRole("button", { name: "Delete project" })).toBeDisabled();
    await page.getByLabel('Type "Renamed project" to confirm').fill("Renamed project");
    await page.getByRole("button", { name: "Delete project" }).click();
    await page.waitForURL("**/projects");
    await expect(page.getByText("Add your first project")).toBeVisible();
  });

  expect(problems, "hidden breakage").toEqual([]);
});

test("the app works on a phone-sized screen", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  const problems = watchForProblems(page);
  await registerNewUser(page);
  const id = await uploadProject(page, zipFixture("plain_php"));
  for (const route of ["", "/architecture", "/inventory", "/graph", "/assistant", "/compare", "/tests", "/docs", "/code", "/settings"]) {
    await page.goto(`/projects/${id}${route}`);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `horizontal page scroll on "${route || "dashboard"}"`).toBeLessThanOrEqual(1);
  }
  for (const route of ["/projects", "/settings", "/account"]) {
    await page.goto(route);
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `horizontal page scroll on "${route}"`).toBeLessThanOrEqual(1);
  }
  await page.goto(`/projects/${id}`);
  await shot(page, "12-mobile-dashboard");
  expect(problems).toEqual([]);
});

test("a large upload passes through the web proxy", async ({ page }) => {
  await registerNewUser(page);
  const padding = "// padding\n".repeat(1_500_000); // About 16 MB, above the proxy's default limit.
  const id = await uploadProject(page, zipFixture("plain_php", { "assets.txt": padding }));
  expect(id).toBeTruthy();
  await expect(page.getByText("Code quality score")).toBeVisible();
});
