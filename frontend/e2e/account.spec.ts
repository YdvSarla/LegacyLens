import { expect, test } from "@playwright/test";
import { PASSWORD, registerNewUser, uploadProject, watchForProblems, zipFixture } from "./helpers";

async function signOut(page: import("@playwright/test").Page) {
  await page.getByRole("button", { name: "Account menu" }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await page.waitForURL("**/login");
}

test("people cannot see each other's projects, and sign out works", async ({ page, context }) => {
  await registerNewUser(page);
  const id = await uploadProject(page, zipFixture("plain_php"));
  await signOut(page);
  await page.goto(`/projects/${id}`);
  await page.waitForURL(/\/login\?next=/);

  await context.clearCookies();
  await registerNewUser(page);
  await page.goto(`/projects/${id}`);
  await expect(page.getByRole("alert").filter({ hasText: "Project not found" })).toBeVisible();
});

test("after sign in, people return to the page they asked for", async ({ page }) => {
  const email = await registerNewUser(page);
  await signOut(page);
  await page.goto("/settings");
  await page.waitForURL(/\/login\?next=%2Fsettings/);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL("**/settings");
});

test("a crafted sign-in link cannot redirect to another site", async ({ page }) => {
  const email = await registerNewUser(page);
  await signOut(page);
  await page.goto("/login?next=/%5Cevil.example.com");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL("**/projects");
  expect(new URL(page.url()).hostname).toBe("localhost");
});

test("sign in shows a clear error for a wrong password", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill("nobody@example.com");
  await page.getByLabel("Password").fill("wrong password here");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Incorrect email or password" })).toBeVisible();
});

test("registration explains problems", async ({ page }) => {
  const email = await registerNewUser(page);
  await signOut(page);
  await page.goto("/register");
  await page.getByLabel("Name").fill("Twin");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "already exists" })).toBeVisible();
});

test("account settings work from start to finish", async ({ page, context }) => {
  const problems = watchForProblems(page);
  const email = await registerNewUser(page);
  await page.getByRole("button", { name: "Account menu" }).click();
  await page.getByRole("menuitem", { name: "Account settings" }).click();
  await page.waitForURL("**/account");

  await page.getByLabel("Name", { exact: true }).fill("New Name");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Name saved.")).toBeVisible();

  await page.getByLabel("Current password").fill("not my password");
  await page.getByLabel("New password", { exact: true }).fill("a brand new passphrase");
  await page.getByLabel("Repeat new password").fill("a different passphrase");
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "do not match" })).toBeVisible();

  await page.getByLabel("Repeat new password").fill("a brand new passphrase");
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "not correct" })).toBeVisible();

  await page.getByLabel("Current password").fill(PASSWORD);
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByText("Password changed.")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Account", exact: true })).toBeVisible();

  const otherDevice = await context.browser()!.newContext({ baseURL: page.url().split("/account")[0] });
  const otherPage = await otherDevice.newPage();
  await otherPage.goto("/login");
  await otherPage.getByLabel("Email").fill(email);
  await otherPage.getByLabel("Password").fill("a brand new passphrase");
  await otherPage.getByRole("button", { name: "Sign in" }).click();
  await otherPage.waitForURL("**/projects");

  await page.getByRole("button", { name: "Sign out everywhere" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Sign out everywhere" }).click();
  await page.waitForURL("**/login");
  await otherPage.goto("/settings");
  await otherPage.waitForURL(/\/login/);
  await otherDevice.close();

  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("a brand new passphrase");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL("**/projects");
  await uploadProject(page, zipFixture("plain_php"));

  await page.goto("/account");
  await page.getByLabel("Enter your password to confirm").fill("wrong");
  await page.getByRole("button", { name: "Delete my account" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "not correct" })).toBeVisible();
  await page.getByLabel("Enter your password to confirm").fill("a brand new passphrase");
  await page.getByRole("button", { name: "Delete my account" }).click();
  await page.waitForURL("**/login");

  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("a brand new passphrase");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Incorrect email or password" })).toBeVisible();
  expect(problems).toEqual([]);
});

test("the theme can be changed and is remembered", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await registerNewUser(page);
  await page.getByRole("button", { name: "Account menu" }).click();
  await page.getByRole("menuitem", { name: "Dark theme" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});
