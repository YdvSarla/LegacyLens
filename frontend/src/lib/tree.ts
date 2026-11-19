export interface Heading {
  level: number;
  text: string;
  id: string;
}

export function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[`*_]/g, "")
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-+|-+$/g, "");
}

/** The contents list of a Markdown document. Headings inside code blocks do not count. */
export function extractHeadings(markdown: string, maxLevel = 2): Heading[] {
  const headings: Heading[] = [];
  let inCode = false;
  for (const line of markdown.split("\n")) {
    if (line.startsWith("```")) inCode = !inCode;
    if (inCode) continue;
    const match = /^(#{1,6})\s+(.+?)\s*$/.exec(line);
    if (match && match[1].length >= 2 && match[1].length <= maxLevel) {
      const text = match[2].replace(/[`*_]/g, "");
      headings.push({ level: match[1].length, text, id: slugify(text) });
    }
  }
  return headings;
}
