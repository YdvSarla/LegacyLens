"use client";

import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import css from "highlight.js/lib/languages/css";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import markdown from "highlight.js/lib/languages/markdown";
import php from "highlight.js/lib/languages/php";
import python from "highlight.js/lib/languages/python";
import sql from "highlight.js/lib/languages/sql";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";
import { useEffect, useMemo, useRef } from "react";

for (const [name, language] of Object.entries({ bash, css, javascript, json, markdown, php, python, sql, typescript, xml, yaml })) {
  hljs.registerLanguage(name, language);
}

const HIGHLIGHT_ALIASES: Record<string, string> = {
  html: "xml",
  shell: "bash",
  toml: "yaml",
  twig: "xml",
  "drupal-info": "yaml",
  requirements: "bash",
};
const LINE_HEIGHT = 20;
const MAX_HIGHLIGHT_CHARS = 400_000;

function escapeHtml(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function CodeView({ content, language, start, end }: {
  content: string;
  language: string;
  start?: number;
  end?: number;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const lineCount = useMemo(() => content.split("\n").length, [content]);
  const lineNumbers = useMemo(() => Array.from({ length: lineCount }, (_, index) => index + 1).join("\n"), [lineCount]);
  const html = useMemo(() => {
    const name = HIGHLIGHT_ALIASES[language] ?? language;
    if (content.length > MAX_HIGHLIGHT_CHARS || !hljs.getLanguage(name)) return escapeHtml(content);
    // highlight.js escapes the source text, so this output is safe to insert.
    return hljs.highlight(content, { language: name, ignoreIllegals: true }).value;
  }, [content, language]);

  useEffect(() => {
    if (start && scroller.current) {
      scroller.current.scrollTop = Math.max((start - 5) * LINE_HEIGHT, 0);
    }
  }, [start, content]);

  const validStart = start && start <= lineCount ? start : undefined;
  const validEnd = validStart ? Math.min(Math.max(end ?? validStart, validStart), lineCount) : undefined;

  return (
    <div ref={scroller} className="max-h-[75vh] overflow-auto rounded-xl border border-line bg-surface" tabIndex={0} aria-label="Source code">
      <div className="relative flex min-w-max font-mono text-xs" style={{ lineHeight: `${LINE_HEIGHT}px` }}>
        {validStart && validEnd && (
          <div
            data-testid="line-highlight"
            className="absolute inset-x-0 border-y border-warning"
            style={{ top: (validStart - 1) * LINE_HEIGHT + 8, height: (validEnd - validStart + 1) * LINE_HEIGHT, background: "var(--code-highlight)" }}
          />
        )}
        {/* One text block rather than one element per line, which matters for long files. */}
        <div aria-hidden className="sticky left-0 z-10 border-r border-line bg-surface py-2 pr-3 pl-3 text-right whitespace-pre text-ink-3 select-none">
          {lineNumbers}
        </div>
        <pre className="relative py-2 pr-6 pl-3"><code dangerouslySetInnerHTML={{ __html: html }} /></pre>
      </div>
    </div>
  );
}
