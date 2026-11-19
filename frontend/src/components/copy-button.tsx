"use client";

import { Check, Copy } from "lucide-react";
import { useState } from "react";

/** The clipboard API needs HTTPS or localhost, so copying falls back to a hidden text area. */
async function copyText(value: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    const area = document.createElement("textarea");
    area.value = value;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.append(area);
    area.select();
    const copied = document.execCommand("copy");
    area.remove();
    return copied;
  }
}

export function CopyButton({ value, label = "Copy", showLabel }: { value: string; label?: string; showLabel?: boolean }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    if (await copyText(value)) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    }
  }
  return (
    <button type="button" onClick={copy} aria-label={copied ? "Copied" : label} className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-xs text-ink-2 hover:bg-surface-2 hover:text-ink">
      {copied ? <Check className="size-3.5 text-good" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
      {showLabel && <span>{copied ? "Copied" : label}</span>}
    </button>
  );
}
