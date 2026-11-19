import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { expect, type Page } from "@playwright/test";

export const FIXTURES = path.resolve(__dirname, "../../backend/tests/fixtures");
export const PASSWORD = "correct horse battery";

const scratchDirectories: string[] = [];
process.on("exit", () => {
  for (const directory of scratchDirectories) rmSync(directory, { recursive: true, force: true });
});

/** Zips a fixture project, optionally replacing files first. */
export function zipFixture(name: string, changes: Record<string, string> = {}): string {
  const scratch = mkdtempSync(path.join(tmpdir(), "legacylens-e2e-"));
  scratchDirectories.push(scratch);
  const copy = path.join(scratch, name);
  execFileSync("cp", ["-r", path.join(FIXTURES, name), copy]);
  for (const [relative, content] of Object.entries(changes)) {
    writeFileSync(path.join(copy, relative), content);
  }
  const archive = path.join(scratch, `${name}.zip`);
  execFileSync("python3", ["-c", `import pathlib,sys,zipfile
root=pathlib.Path(sys.argv[2])
with zipfile.ZipFile(sys.argv[1],"w",zipfile.ZIP_STORED) as z:
    for f in sorted(root.rglob("*")):
        if f.is_file(): z.write(f,f.relative_to(root))`, archive, copy]);
  return archive;
}

export function textFile(name: string, content: string): string {
  const scratch = mkdtempSync(path.join(tmpdir(), "legacylens-e2e-"));
  scratchDirectories.push(scratch);
  const file = path.join(scratch, name);
  writeFileSync(file, content);
  return file;
}

export async function registerNewUser(page: Page): Promise<string> {
  const email = `e2e-${Date.now()}-${Math.floor(Math.random() * 10000)}@example.com`;
  await page.goto("/register");
  await page.getByLabel("Name").fill("E2E User");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await page.waitForURL("**/projects");
  return email;
}

export async function uploadProject(page: Page, archive: string): Promise<string> {
  await page.getByRole("button", { name: "Add project" }).first().click();
  await page.getByRole("tab", { name: "ZIP upload" }).click();
  await page.getByLabel("ZIP archive").setInputFiles(archive);
  await page.getByRole("button", { name: "Upload and analyze" }).click();
  await page.waitForURL(/\/projects\/[0-9a-f-]{36}$/);
  await page.getByRole("heading", { name: "Dashboard" }).waitFor({ timeout: 200_000 });
  return page.url().split("/projects/")[1];
}

export async function waitUntilReady(page: Page, id: string, expectedCommits?: number) {
  await expect(async () => {
    if (expectedCommits !== undefined) {
      const commits = await (await page.request.get(`/api/projects/${id}/commits`)).json();
      expect(commits).toHaveLength(expectedCommits);
    }
    const project = await (await page.request.get(`/api/projects/${id}`)).json();
    expect(project.status).toBe("ready");
  }).toPass({ timeout: 180_000 });
}

/** Records anything that should never happen, so every test also fails on hidden breakage. */
export function watchForProblems(page: Page): string[] {
  const problems: string[] = [];
  page.on("pageerror", (error) => problems.push(`page error: ${error.message}`));
  page.on("console", (message) => {
    // A failed request is logged by the browser even when the app handles it correctly.
    if (message.type() === "error" && !message.text().startsWith("Failed to load resource")) {
      problems.push(`console: ${message.text()}`);
    }
  });
  page.on("response", (response) => {
    if (response.status() >= 500) problems.push(`server error ${response.status()}: ${response.url()}`);
  });
  return problems;
}
