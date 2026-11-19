import { describe, expect, it } from "vitest";
import { safeRedirect } from "./safe-redirect";

describe("safeRedirect", () => {
  it("keeps paths on this site", () => {
    expect(safeRedirect("/projects/1/graph?x=1")).toBe("/projects/1/graph?x=1");
  });

  it.each(["https://evil.com", "//evil.com", "/\\evil.com", "\\\\evil.com", "/\t/evil.com", "javascript:alert(1)", "evil.com", "", null])(
    "refuses %s",
    (value) => {
      expect(safeRedirect(value)).toBe("/projects");
    },
  );
});
