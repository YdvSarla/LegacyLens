import { expect, test } from "@playwright/test";
import { registerNewUser, textFile, uploadProject, waitUntilReady, watchForProblems, zipFixture } from "./helpers";

test("a bad ZIP explains the problem", async ({ page }) => {
  await registerNewUser(page);
  await page.getByRole("button", { name: "Add project" }).first().click();
  await page.getByRole("tab", { name: "ZIP upload" }).click();
  await page.getByLabel("ZIP archive").setInputFiles(textFile("notes.txt", "hello"));
  await expect(page.getByRole("alert").filter({ hasText: "choose a .zip file" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Upload and analyze" })).toBeDisabled();

  await page.getByLabel("ZIP archive").setInputFiles(textFile("broken.zip", "not really a zip"));
  await page.getByRole("button", { name: "Upload and analyze" }).click();
  await page.waitForURL(/\/projects\/[0-9a-f-]{36}$/);
  await expect(page.getByRole("heading", { name: /could not be imported/ })).toBeVisible();
  await expect(page.getByText("not a valid ZIP")).toBeVisible();
  await page.getByRole("link", { name: "Project settings" }).click();
  await expect(page.getByRole("heading", { name: "Project settings" })).toBeVisible();
});

test("a bad update keeps the existing project usable", async ({ page }) => {
  const problems = watchForProblems(page);
  await registerNewUser(page);
  const id = await uploadProject(page, zipFixture("plain_php"));
  await page.goto(`/projects/${id}/settings`);
  await page.getByLabel("New ZIP version").setInputFiles(textFile("broken.zip", "not really a zip"));
  await page.getByRole("button", { name: "Upload version" }).click();
  await expect(page.getByText("The last update failed.")).toBeVisible({ timeout: 60_000 });

  await page.goto(`/projects/${id}/code?path=index.php`);
  await expect(page.locator("pre code")).toContainText("require_once");

  await page.getByRole("button", { name: "Analyze again" }).click();
  await waitUntilReady(page, id);
  await page.reload();
  await expect(page.getByText("The last update failed.")).toHaveCount(0);
  expect(problems).toEqual([]);
});

test("importing a GitHub URL that is not valid is refused with a message", async ({ page }) => {
  await registerNewUser(page);
  await page.getByRole("button", { name: "Add project" }).first().click();
  await page.getByLabel("Repository URL").fill("http://internal.example/owner/repo");
  await page.getByRole("button", { name: "Import repository" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Enter a GitHub repository URL" })).toBeVisible();
});

test("a failed GitHub import can be retried and removed", async ({ page }) => {
  const problems = watchForProblems(page);
  await registerNewUser(page);
  await page.getByRole("button", { name: "Add project" }).first().click();
  await page.getByLabel("Repository URL").fill("https://github.com/legacylens-e2e-no-such-owner/no-such-repo");
  await page.getByRole("button", { name: "Import repository" }).click();
  await page.waitForURL(/\/projects\/[0-9a-f-]{36}$/);
  await expect(page.getByRole("heading", { name: /could not be imported/ })).toBeVisible({ timeout: 90_000 });
  await page.getByRole("button", { name: "Try the import again" }).click();
  await expect(page.getByRole("heading", { name: /could not be imported/ })).toBeVisible({ timeout: 90_000 });
  await page.getByRole("link", { name: "Project settings" }).click();
  await page.getByLabel(/Type ".*" to confirm/).fill("legacylens-e2e-no-such-owner/no-such-repo");
  await page.getByRole("button", { name: "Delete project" }).click();
  await page.waitForURL("**/projects");
  expect(problems).toEqual([]);
});

test("a real GitHub repository can be imported through the interface", async ({ page }) => {
  test.skip(!process.env.E2E_NETWORK, "Needs internet access. Set E2E_NETWORK=1.");
  const problems = watchForProblems(page);
  await registerNewUser(page);
  await page.getByRole("button", { name: "Add project" }).first().click();
  await page.getByLabel("Repository URL").fill("https://github.com/hagopj13/node-express-boilerplate");
  await page.getByRole("button", { name: "Import repository" }).click();
  await page.waitForURL(/\/projects\/[0-9a-f-]{36}$/);
  await page.getByRole("heading", { name: "Dashboard" }).waitFor({ timeout: 200_000 });
  await expect(page.getByText("Express", { exact: true })).toBeVisible();
  const id = page.url().split("/projects/")[1];
  await page.goto(`/projects/${id}/inventory?tab=endpoints`);
  await expect(page.getByText("/register")).toBeVisible();
  await page.goto(`/projects/${id}/compare`);
  await page.getByRole("button", { name: "Compare", exact: true }).click();
  await expect(page.getByText("Change risk")).toBeVisible({ timeout: 60_000 });
  expect(problems).toEqual([]);
});

test("unknown pages and security headers", async ({ page }) => {
  await page.goto("/this-page-does-not-exist");
  await expect(page.getByRole("heading", { name: "This page does not exist" })).toBeVisible();
  const response = await page.goto("/login");
  const headers = response!.headers();
  expect(headers["content-security-policy"]).toContain("frame-ancestors 'none'");
  expect(headers["x-frame-options"]).toBe("DENY");
  expect(headers["x-powered-by"]).toBeUndefined();
});

test("a request from another site cannot use the session", async ({ page }) => {
  await registerNewUser(page);
  const attack = await page.request.post("/api/auth/logout-all", { headers: { Origin: "https://evil.example.com" } });
  expect(attack.status()).toBe(403);
  const session = await page.request.get("/api/auth/me");
  expect(session.status()).toBe(200);
  const allowed = await page.evaluate(async () => (await fetch("/api/auth/me", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: "Same site" }) })).status);
  expect(allowed).toBe(200);
});
