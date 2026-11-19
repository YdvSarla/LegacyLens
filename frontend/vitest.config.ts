import { defineConfig } from "vitest/config";

// Browser tests in e2e/ belong to Playwright.
export default defineConfig({
  test: { include: ["src/**/*.test.ts"] },
});
