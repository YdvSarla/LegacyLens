import type { Citation } from "./types";

const CITATION = /\[([^\[\]\n:]+?):(\d+)(?:-(\d+))?\]/g;

export function codeHref(projectId: string, path: string, start?: number | null, end?: number | null): string {
  const params = new URLSearchParams({ path });
  if (start) params.set("start", String(start));
  if (end) params.set("end", String(end));
  return `/projects/${projectId}/code?${params.toString()}`;
}

/**
 * Turns verified `[path:10-20]` citations into Markdown links to the code viewer.
 * Anything not on the verified list stays plain text, so a link is never invented.
 */
export function linkCitations(content: string, citations: Citation[], projectId: string): string {
  const verified = new Set(citations.map((c) => `${c.path}:${c.start_line}-${c.end_line}`));
  return content.replace(CITATION, (match, path: string, start: string, end?: string) => {
    const key = `${path}:${start}-${end ?? start}`;
    if (!verified.has(key)) return match;
    const label = end && end !== start ? `${path}:${start}-${end}` : `${path}:${start}`;
    return `[${label}](${codeHref(projectId, path, Number(start), Number(end ?? start))})`;
  });
}
