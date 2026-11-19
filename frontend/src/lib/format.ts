export function compact(value: number): string {
  if (value < 1000) return value.toLocaleString("en-US");
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

export function shortSha(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 8) : "";
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

const LANGUAGE_LABELS: Record<string, string> = {
  php: "PHP",
  python: "Python",
  javascript: "JavaScript",
  typescript: "TypeScript",
};

export function languageLabel(language: string | null | undefined): string {
  if (!language) return "Unknown";
  return LANGUAGE_LABELS[language] ?? language.charAt(0).toUpperCase() + language.slice(1);
}

/** Fixed color per language, so an entity keeps its color across every view. */
export function languageColor(language: string | null | undefined): string {
  const slots: Record<string, string> = {
    php: "var(--series-1)",
    javascript: "var(--series-4)",
    typescript: "var(--series-3)",
    python: "var(--series-2)",
  };
  return slots[language ?? ""] ?? "var(--series-5)";
}
