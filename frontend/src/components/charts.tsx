"use client";

import { useState } from "react";
import { compact } from "@/lib/format";

export interface BarItem {
  key: string;
  label: string;
  value: number;
  /** Fill color. Defaults to one color for all bars, because bar length already shows size. */
  color?: string;
  detail?: string;
}

/**
 * Horizontal bars. Values are direct labels, so the numbers are always readable.
 * Each row is a focusable hover target larger than the painted bar.
 */
export function BarList({ items, ariaLabel, valueSuffix = "" }: {
  items: BarItem[];
  ariaLabel: string;
  valueSuffix?: string;
}) {
  const [active, setActive] = useState<string | null>(null);
  const max = Math.max(...items.map((item) => item.value), 1);
  return (
    <ul aria-label={ariaLabel} className="space-y-0.5">
      {items.map((item) => (
        <li
          key={item.key}
          tabIndex={0}
          onMouseEnter={() => setActive(item.key)}
          onMouseLeave={() => setActive(null)}
          onFocus={() => setActive(item.key)}
          onBlur={() => setActive(null)}
          className="relative grid grid-cols-[minmax(4.5rem,8rem)_1fr_auto] items-center gap-2 rounded-md px-1 py-1 hover:bg-surface-2 focus:bg-surface-2"
        >
          <span className="truncate text-xs text-ink-2">{item.label}</span>
          <span className="block h-2.5">
            {item.value > 0 && (
              <span
                className="block h-full rounded-r"
                style={{
                  width: `${Math.max((item.value / max) * 100, 1.5)}%`,
                  background: item.color ?? "var(--series-1)",
                }}
              />
            )}
          </span>
          <span className="text-xs font-medium tabular-nums">
            {compact(item.value)}
            {valueSuffix}
          </span>
          {active === item.key && (
            <span
              role="tooltip"
              className="pointer-events-none absolute top-full left-24 z-20 mt-1 rounded-md border border-line-strong bg-surface px-2.5 py-1.5 text-xs whitespace-nowrap shadow-lg"
            >
              <strong className="font-semibold">{item.value.toLocaleString("en-US")}{valueSuffix}</strong>{" "}
              <span className="text-ink-2">{item.detail ?? item.label}</span>
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

/** Column chart for ordered buckets, using a one-hue ramp from light to dark. */
export function OrderedColumns({ items, ariaLabel, xLabel }: {
  items: { key: string; label: string; value: number; color: string }[];
  ariaLabel: string;
  xLabel: string;
}) {
  const [active, setActive] = useState<string | null>(null);
  const max = Math.max(...items.map((item) => item.value), 1);
  return (
    <figure aria-label={ariaLabel}>
      <div className="flex h-36 items-end gap-1.5">
        {items.map((item) => (
          <div
            key={item.key}
            tabIndex={0}
            onMouseEnter={() => setActive(item.key)}
            onMouseLeave={() => setActive(null)}
            onFocus={() => setActive(item.key)}
            onBlur={() => setActive(null)}
            className="relative flex h-full flex-1 flex-col items-center justify-end rounded-md hover:bg-surface-2 focus:bg-surface-2"
          >
            <span className="mb-1 text-xs font-medium tabular-nums">{compact(item.value)}</span>
            {item.value > 0 && (
              <span
                className="block w-full max-w-10 rounded-t"
                style={{ height: `${Math.max((item.value / max) * 76, 2)}%`, background: item.color }}
              />
            )}
            {active === item.key && (
              <span role="tooltip" className="pointer-events-none absolute bottom-full z-20 mb-1 rounded-md border border-line-strong bg-surface px-2.5 py-1.5 text-xs whitespace-nowrap shadow-lg">
                <strong className="font-semibold">{item.value.toLocaleString("en-US")}</strong>{" "}
                <span className="text-ink-2">functions at complexity {item.label}</span>
              </span>
            )}
          </div>
        ))}
      </div>
      <div className="mt-1 flex gap-1.5 border-t border-line pt-1">
        {items.map((item) => (
          <span key={item.key} className="flex-1 text-center text-xs text-ink-3">{item.label}</span>
        ))}
      </div>
      <figcaption className="mt-1 text-center text-xs text-ink-3">{xLabel}</figcaption>
    </figure>
  );
}

/** A labeled score meter. The track is a lighter step of the same ramp as the fill. */
export function ScoreMeter({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between text-xs">
        <span className="text-ink-2">{label}</span>
        <span className="font-semibold tabular-nums">{value}</span>
      </div>
      <div
        role="meter"
        aria-label={label}
        aria-valuenow={value}
        aria-valuemin={0}
        aria-valuemax={100}
        className="h-2 overflow-hidden rounded"
        style={{ background: "var(--meter-track)" }}
      >
        <div
          className="h-full rounded"
          style={{ width: `${Math.max(value, 1)}%`, background: "var(--series-1)" }}
        />
      </div>
    </div>
  );
}

export function StatTile({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="rounded-xl border border-line bg-surface p-3.5">
      <div className="text-xs text-ink-2">{label}</div>
      <div className="mt-0.5 text-2xl font-semibold">{typeof value === "number" ? compact(value) : value}</div>
      {sub && <div className="mt-0.5 text-xs text-ink-3">{sub}</div>}
    </div>
  );
}
