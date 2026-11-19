/**
 * Only a path on this site is accepted, so a crafted sign-in link cannot send people elsewhere.
 * Browsers read a backslash as a slash, which makes "/\evil.com" a link to another site.
 */
export function safeRedirect(value: string | null | undefined, fallback = "/projects"): string {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.includes("\\")) return fallback;
  // Control characters are removed by browsers before parsing a URL.
  if (/[\u0000-\u001f]/.test(value)) return fallback;
  return value;
}
