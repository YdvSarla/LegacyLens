"use client";

import { Children, isValidElement, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { slugify } from "@/lib/tree";
import { CopyButton } from "./copy-button";

function plainText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(plainText).join("");
  if (isValidElement(node)) return plainText((node.props as { children?: ReactNode }).children);
  return "";
}

/** Raw HTML is not rendered, so AI output and repository text cannot inject markup. */
export function Markdown({ children }: { children: string }) {
  return (
    <div className="prose-doc text-sm">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          table: ({ children: content }) => (
            <div className="table-wrap"><table>{content}</table></div>
          ),
          h2: ({ children: content }) => <h2 id={slugify(plainText(content))}>{content}</h2>,
          pre: ({ children: content }) => (
            <pre>
              <span className="absolute top-1.5 right-1.5"><CopyButton value={plainText(Children.toArray(content)).replace(/\n$/, "")} label="Copy code" /></span>
              {content}
            </pre>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
