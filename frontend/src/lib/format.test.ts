import { describe, expect, it } from "vitest";
import { codeHref, linkCitations } from "./citations";
import { compact, languageLabel, shortSha } from "./format";

describe("linkCitations", () => {
  const citations = [{ path: "app/a.php", start_line: 10, end_line: 20 }];

  it("links a verified citation to the code viewer", () => {
    const output = linkCitations("See [app/a.php:10-20].", citations, "p1");
    expect(output).toBe("See [app/a.php:10-20](/projects/p1/code?path=app%2Fa.php&start=10&end=20).");
  });

  it("does not link a citation that was not verified", () => {
    expect(linkCitations("See [fake.php:1-2].", citations, "p1")).toBe("See [fake.php:1-2].");
  });

  it("does not touch an existing Markdown link label pattern without a line", () => {
    expect(linkCitations("A [note] here", citations, "p1")).toBe("A [note] here");
  });

  it("handles a single line citation", () => {
    const single = [{ path: "a.py", start_line: 3, end_line: 3 }];
    expect(linkCitations("[a.py:3]", single, "p")).toBe("[a.py:3](/projects/p/code?path=a.py&start=3&end=3)");
  });
});

describe("codeHref", () => {
  it("encodes paths", () => {
    expect(codeHref("p", "a b/c.php")).toBe("/projects/p/code?path=a+b%2Fc.php");
  });
});

describe("format helpers", () => {
  it("compacts large numbers", () => {
    expect(compact(950)).toBe("950");
    expect(compact(12900)).toBe("12.9K");
  });

  it("shortens commit ids", () => {
    expect(shortSha("abcdef1234567")).toBe("abcdef12");
    expect(shortSha(null)).toBe("");
  });

  it("labels languages", () => {
    expect(languageLabel("php")).toBe("PHP");
    expect(languageLabel("twig")).toBe("Twig");
    expect(languageLabel(null)).toBe("Unknown");
  });
});
