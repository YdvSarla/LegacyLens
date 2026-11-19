"use client";

import { AlertCircle, AlertTriangle, CheckCircle2, ChevronLeft, ChevronRight, CircleX, Info, Loader2, Search, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { errorMessage } from "@/lib/api";
import type { Severity } from "@/lib/types";

export function Card({ title, action, children, className = "" }: {
  title?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`min-w-0 rounded-xl border border-line bg-surface shadow-[0_1px_2px_rgb(0_0_0/0.04)] ${className}`}>
      {(title || action) && (
        <header className="flex items-center justify-between gap-3 px-4 pt-3.5 pb-1">
          <h2 className="text-sm font-semibold">{title}</h2>
          {action}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

type ButtonVariant = "primary" | "secondary" | "danger" | "ghost";

const BUTTON_STYLES: Record<ButtonVariant, string> = {
  primary: "bg-accent text-on-accent hover:bg-accent-hover",
  secondary: "border border-line-strong bg-surface hover:bg-surface-2 text-ink",
  danger: "border border-critical text-critical-text hover:bg-critical-soft",
  ghost: "text-ink-2 hover:bg-surface-2 hover:text-ink",
};

export function Button({ variant = "secondary", busy, children, className = "", ...props }: {
  variant?: ButtonVariant;
  busy?: boolean;
} & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...props}
      disabled={props.disabled || busy}
      className={`inline-flex min-h-9 items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${BUTTON_STYLES[variant]} ${className}`}
    >
      {busy && <Loader2 className="size-4 animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

export const inputClass =
  "w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-sm text-ink placeholder:text-ink-3";

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div>
      <label className="block">
        <span className="mb-1 block text-sm font-medium">{label}</span>
        {children}
      </label>
      {hint && <p className="mt-1 text-xs text-ink-3">{hint}</p>}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center justify-center gap-2 p-10 text-sm text-ink-3">
      <Loader2 className="size-4 animate-spin" aria-hidden />
      {label}
    </div>
  );
}

export function ErrorNotice({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex items-start gap-2 rounded-lg border border-critical bg-critical-soft p-3 text-sm">
      <CircleX className="mt-0.5 size-4 shrink-0 text-critical" aria-hidden />
      <div className="min-w-0 flex-1">{errorMessage(error)}</div>
      {onRetry && <Button variant="ghost" onClick={onRetry}>Try again</Button>}
    </div>
  );
}

export function Notice({ tone = "info", children }: { tone?: "info" | "warning" | "good"; children: ReactNode }) {
  const styles = {
    info: "border-line-strong bg-surface-2",
    warning: "border-warning bg-warning-soft",
    good: "border-good bg-good-soft",
  };
  const Icon = tone === "warning" ? AlertTriangle : tone === "good" ? CheckCircle2 : Info;
  return (
    <div className={`flex items-start gap-2 rounded-lg border p-3 text-sm ${styles[tone]}`}>
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-line-strong p-10 text-center">
      <p className="font-medium">{title}</p>
      {children && <div className="mx-auto mt-2 max-w-md text-sm text-ink-3">{children}</div>}
    </div>
  );
}

export function Badge({ children, tone = "neutral", title }: {
  children: ReactNode;
  tone?: "neutral" | "accent" | "good" | "warning" | "critical";
  title?: string;
}) {
  const styles = {
    neutral: "bg-surface-2 text-ink-2",
    accent: "bg-accent-soft text-ink",
    good: "bg-good-soft text-ink",
    warning: "bg-warning-soft text-ink",
    critical: "bg-critical-soft text-ink",
  };
  return (
    <span title={title} className={`inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium ${styles[tone]}`}>
      {children}
    </span>
  );
}

const SEVERITY = {
  critical: { Icon: CircleX, color: "text-critical", label: "Critical" },
  high: { Icon: AlertCircle, color: "text-serious", label: "High" },
  medium: { Icon: AlertTriangle, color: "text-warning", label: "Medium" },
  low: { Icon: Info, color: "text-ink-3", label: "Low" },
} as const;

/** A status color never carries meaning alone: it always comes with an icon and a label. */
export function SeverityBadge({ severity }: { severity: Severity }) {
  const { Icon, color, label } = SEVERITY[severity];
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap text-xs font-medium">
      <Icon className={`size-3.5 ${color}`} aria-hidden />
      {label}
    </span>
  );
}

export function CodeLink({ projectId, path, line, endLine, children }: {
  projectId: string;
  path: string;
  line?: number | null;
  endLine?: number | null;
  children?: ReactNode;
}) {
  const params = new URLSearchParams({ path });
  if (line) {
    params.set("start", String(line));
    params.set("end", String(endLine ?? line));
  }
  return (
    <Link
      href={`/projects/${projectId}/code?${params.toString()}`}
      className="font-mono text-xs break-all text-accent hover:underline"
    >
      {children ?? (line ? `${path}:${line}` : path)}
    </Link>
  );
}

export function PageHeader({ title, description, actions }: { title: ReactNode; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-0.5 max-w-3xl text-sm text-ink-2">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return <div aria-hidden className={`skeleton ${className}`} />;
}

export function CardsSkeleton({ count = 3, className = "h-28" }: { count?: number; className?: string }) {
  return (
    <div role="status" aria-label="Loading" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {Array.from({ length: count }, (_, index) => <Skeleton key={index} className={className} />)}
    </div>
  );
}

export function TableSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <div role="status" aria-label="Loading" className="space-y-2">
      {Array.from({ length: rows }, (_, index) => <Skeleton key={index} className="h-8" />)}
    </div>
  );
}

/** Text filter that waits for a pause in typing, so each keystroke does not trigger a request. */
export function SearchInput({ value, onChange, label, placeholder, className = "" }: {
  value: string;
  onChange: (value: string) => void;
  label: string;
  placeholder?: string;
  className?: string;
}) {
  const [draft, setDraft] = useState(value);
  const callback = useRef(onChange);
  useEffect(() => {
    callback.current = onChange;
  });
  useEffect(() => {
    if (draft === value) return;
    const timer = window.setTimeout(() => callback.current(draft), 250);
    return () => window.clearTimeout(timer);
  }, [draft, value]);
  return (
    <div className={`relative ${className}`}>
      <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-ink-3" aria-hidden />
      <input
        type="search"
        aria-label={label}
        value={draft}
        placeholder={placeholder ?? label}
        onChange={(event) => setDraft(event.target.value)}
        className={`${inputClass} pl-8`}
      />
    </div>
  );
}

export function Pager({ total, limit, offset, onChange }: {
  total: number;
  limit: number;
  offset: number;
  onChange: (offset: number) => void;
}) {
  if (total === 0) return null;
  const end = Math.min(offset + limit, total);
  return (
    <div className="mt-3 flex items-center justify-between gap-2 text-xs text-ink-2">
      <span className="tabular-nums">{offset + 1} to {end} of {total.toLocaleString("en-US")}</span>
      {total > limit && (
        <div className="flex gap-1">
          <Button aria-label="Previous page" disabled={offset === 0} onClick={() => onChange(Math.max(offset - limit, 0))}>
            <ChevronLeft className="size-4" aria-hidden />
          </Button>
          <Button aria-label="Next page" disabled={end >= total} onClick={() => onChange(offset + limit)}>
            <ChevronRight className="size-4" aria-hidden />
          </Button>
        </div>
      )}
    </div>
  );
}

export function Modal({ title, onClose, children, wide }: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  useEffect(() => {
    closeRef.current = onClose;
  });
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const focusable = panel.current?.querySelector<HTMLElement>("input, select, textarea, button:not([data-close])");
    (focusable ?? panel.current)?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") closeRef.current();
      if (event.key !== "Tab" || !panel.current) return;
      const items = [...panel.current.querySelectorAll<HTMLElement>("a[href], button:not([disabled]), input:not([disabled]), select, textarea")];
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus?.();
    };
  }, []);
  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center overflow-y-auto bg-black/40 p-4 pt-[10vh]" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div ref={panel} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} className={`animate-in w-full ${wide ? "max-w-2xl" : "max-w-md"} rounded-xl border border-line-strong bg-surface shadow-xl`}>
        <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          <h2 className="text-sm font-semibold">{title}</h2>
          <button type="button" data-close aria-label="Close" onClick={onClose} className="grid size-7 place-items-center rounded-md text-ink-2 hover:bg-surface-2">
            <X className="size-4" aria-hidden />
          </button>
        </div>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}

export function ConfirmDialog({ title, children, confirmLabel, busy, onConfirm, onCancel }: {
  title: string;
  children: ReactNode;
  confirmLabel: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Modal title={title} onClose={onCancel}>
      <div className="text-sm text-ink-2">{children}</div>
      <div className="mt-4 flex justify-end gap-2">
        <Button onClick={onCancel}>Cancel</Button>
        <Button variant="danger" busy={busy} onClick={onConfirm}>{confirmLabel}</Button>
      </div>
    </Modal>
  );
}

export function usePageTitle(title: string) {
  useEffect(() => {
    document.title = `${title} · LegacyLens`;
  }, [title]);
}

export function TableWrap({ children }: { children: ReactNode }) {
  return <div className="overflow-x-auto">{children}</div>;
}

export const thClass = "border-b border-line-strong px-2 py-1.5 text-left text-xs font-medium whitespace-nowrap text-ink-2";
export const tdClass = "border-b border-line px-2 py-1.5 align-top text-sm";
