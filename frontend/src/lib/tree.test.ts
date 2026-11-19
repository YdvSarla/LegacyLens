import { describe, expect, it } from "vitest";
import { extractHeadings, slugify } from "./tree";

describe("slugify", () => {
  it("makes stable ids", () => {
    expect(slugify("HTTP API reference")).toBe("http-api-reference");
    expect(slugify("`App\\Models\\User`")).toBe("app-models-user");
  });
});

describe("extractHeadings", () => {
  it("lists section headings and ignores code blocks", () => {
    const markdown = "# Title\n\n## One\n\n```\n## not a heading\n```\n\n### Deep\n\n## Two `x`\n";
    expect(extractHeadings(markdown)).toEqual([
      { level: 2, text: "One", id: "one" },
      { level: 2, text: "Two x", id: "two-x" },
    ]);
  });
});
